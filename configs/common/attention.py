# Copyright (c) OpenMMLab. All rights reserved.
import warnings
import itertools
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from ..basic.build_layer import build_dropout
from core.initialize.weight_init import trunc_normal_
from .base_module import BaseModule
from .layer_scale import LayerScale

from .helpers import to_2tuple
from utils.version_utils import digit_version

if digit_version(torch.__version__) >= digit_version('1.10.0'):
    from functools import partial
    torch_meshgrid = partial(torch.meshgrid, indexing='ij')
else:
    torch_meshgrid = torch.meshgrid

class SE(nn.Module):
    def __init__(self, channel, ratio=16):
        super(SE, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
                nn.Linear(channel, channel // ratio, bias=False),
                nn.ReLU(inplace=True),
                nn.Linear(channel // ratio, channel, bias=False),
                nn.Sigmoid()
        )
    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y

# CBAM
class ChannelAttentionModule(nn.Module):
    def __init__(self, channel, ratio=16):
        super(ChannelAttentionModule, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)

        self.shared_MLP = nn.Sequential(
            nn.Conv2d(channel, channel // ratio, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(channel // ratio, channel, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avgout = self.shared_MLP(self.avg_pool(x))
#         print(avgout.shape)
        maxout = self.shared_MLP(self.max_pool(x))
        return self.sigmoid(avgout + maxout)


class SpatialAttentionModule(nn.Module):
    def __init__(self):
        super(SpatialAttentionModule, self).__init__()
        self.conv2d = nn.Conv2d(in_channels=2, out_channels=1, kernel_size=7, stride=1, padding=3)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avgout = torch.mean(x, dim=1, keepdim=True)
        maxout, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avgout, maxout], dim=1)
        out = self.sigmoid(self.conv2d(out))
        return out


class CBAM(nn.Module):
    def __init__(self, channel):
        super(CBAM, self).__init__()
        self.channel_attention = ChannelAttentionModule(channel)
        self.spatial_attention = SpatialAttentionModule()

    def forward(self, x):
        out = self.channel_attention(x) * x
#         print('outchannels:{}'.format(out.shape))
        out = self.spatial_attention(out) * out
        return out


# TA

class BasicConv(nn.Module):
    def __init__(
        self,
        in_planes,
        out_planes,
        kernel_size,
        stride=1,
        padding=0,
        dilation=1,
        groups=1,
        relu=True,
        bn=True,
        bias=False,
    ):
        super(BasicConv, self).__init__()
        self.out_channels = out_planes
        self.conv = nn.Conv2d(
            in_planes,
            out_planes,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            bias=bias,
        )
        self.bn = (
            nn.BatchNorm2d(out_planes, eps=1e-5, momentum=0.01, affine=True)
            if bn
            else None
        )
        self.relu = nn.ReLU() if relu else None

    def forward(self, x):
        x = self.conv(x)
        if self.bn is not None:
            x = self.bn(x)
        if self.relu is not None:
            x = self.relu(x)
        return x

class ChannelPool(nn.Module):
    def forward(self, x):
        return torch.cat(
            (torch.max(x, 1)[0].unsqueeze(1), torch.mean(x, 1).unsqueeze(1)), dim=1
        )


class SpatialGate(nn.Module):
    def __init__(self):
        super(SpatialGate, self).__init__()
        kernel_size = 7
        self.compress = ChannelPool()
        self.spatial = BasicConv(
            2, 1, kernel_size, stride=1, padding=(kernel_size - 1) // 2, relu=False
        )

    def forward(self, x):
        x_compress = self.compress(x)
        x_out = self.spatial(x_compress)
        scale = torch.sigmoid_(x_out)
        return x * scale


class TripletAttention(nn.Module):
    def __init__(
        self,
        gate_channels,
        reduction_ratio=16,
        pool_types=["avg", "max"],
        no_spatial=False,
    ):
        super(TripletAttention, self).__init__()
        self.ChannelGateH = SpatialGate()
        self.ChannelGateW = SpatialGate()
        self.no_spatial = no_spatial
        if not no_spatial:
            self.SpatialGate = SpatialGate()

    def forward(self, x):
        x_perm1 = x.permute(0, 2, 1, 3).contiguous()
        x_out1 = self.ChannelGateH(x_perm1)
        x_out11 = x_out1.permute(0, 2, 1, 3).contiguous()
        x_perm2 = x.permute(0, 3, 2, 1).contiguous()
        x_out2 = self.ChannelGateW(x_perm2)
        x_out21 = x_out2.permute(0, 3, 2, 1).contiguous()
        if not self.no_spatial:
            x_out = self.SpatialGate(x)
            x_out = (1 / 3) * (x_out + x_out11 + x_out21)
        else:
            x_out = (1 / 2) * (x_out11 + x_out21)
        return x_out

# CA

class h_sigmoid(nn.Module):
    def __init__(self, inplace=True):
        super(h_sigmoid, self).__init__()
        self.relu = nn.ReLU6(inplace=inplace)

    def forward(self, x):
        return self.relu(x + 3) / 6


class h_swish(nn.Module):
    def __init__(self, inplace=True):
        super(h_swish, self).__init__()
        self.sigmoid = h_sigmoid(inplace=inplace)

    def forward(self, x):
        return x * self.sigmoid(x)


class CoordAttention(nn.Module):
    def __init__(self, inp, oup, reduction=32):
        super(CoordAttention, self).__init__()
        self.pool_h = nn.AdaptiveAvgPool2d((None, 1))
        self.pool_w = nn.AdaptiveAvgPool2d((1, None))

        mip = max(8, inp // reduction)

        self.conv1 = nn.Conv2d(inp, mip, kernel_size=1, stride=1, padding=0)
        self.bn1 = nn.BatchNorm2d(mip)
        self.act = h_swish()

        self.conv_h = nn.Conv2d(mip, oup, kernel_size=1, stride=1, padding=0)
        self.conv_w = nn.Conv2d(mip, oup, kernel_size=1, stride=1, padding=0)

    def forward(self, x):
        identity = x

        n, c, h, w = x.size()
        x_h = self.pool_h(x)
        x_w = self.pool_w(x).permute(0, 1, 3, 2)

        y = torch.cat([x_h, x_w], dim=2)
        y = self.conv1(y)
        y = self.bn1(y)
        y = self.act(y)

        x_h, x_w = torch.split(y, [h, w], dim=2)
        x_w = x_w.permute(0, 1, 3, 2)

        a_h = self.conv_h(x_h).sigmoid()
        a_w = self.conv_w(x_w).sigmoid()

		# 如果下面这个原论文代码用不了的话，可以换成另一个试试
        out = identity * a_w * a_h
        # out = a_h.expand_as(x) * a_w.expand_as(x) * identity

        return out
class WindowMSA(BaseModule):
    """Window based multi-head self-attention (W-MSA) module with relative
    position bias.

    Args:
        embed_dims (int): Number of input channels.
        window_size (tuple[int]): The height and width of the window.
        num_heads (int): Number of attention heads.
        qkv_bias (bool, optional): If True, add a learnable bias to q, k, v.
            Defaults to True.
        qk_scale (float, optional): Override default qk scale of
            ``head_dim ** -0.5`` if set. Defaults to None.
        attn_drop (float, optional): Dropout ratio of attention weight.
            Defaults to 0.
        proj_drop (float, optional): Dropout ratio of output. Defaults to 0.
        init_cfg (dict, optional): The extra config for initialization.
            Defaults to None.
    """

    def __init__(self,
                 embed_dims,
                 window_size,
                 num_heads,
                 qkv_bias=True,
                 qk_scale=None,
                 attn_drop=0.,
                 proj_drop=0.,
                 init_cfg=None):

        super().__init__(init_cfg)
        self.embed_dims = embed_dims
        self.window_size = window_size  # Wh, Ww
        self.num_heads = num_heads
        head_embed_dims = embed_dims // num_heads
        self.scale = qk_scale or head_embed_dims**-0.5

        # define a parameter table of relative position bias
        self.relative_position_bias_table = nn.Parameter(
            torch.zeros((2 * window_size[0] - 1) * (2 * window_size[1] - 1),
                        num_heads))  # 2*Wh-1 * 2*Ww-1, nH

        # About 2x faster than original impl
        Wh, Ww = self.window_size
        rel_index_coords = self.double_step_seq(2 * Ww - 1, Wh, 1, Ww)
        rel_position_index = rel_index_coords + rel_index_coords.T
        rel_position_index = rel_position_index.flip(1).contiguous()
        self.register_buffer('relative_position_index', rel_position_index)

        self.qkv = nn.Linear(embed_dims, embed_dims * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(embed_dims, embed_dims)
        self.proj_drop = nn.Dropout(proj_drop)

        self.softmax = nn.Softmax(dim=-1)

    def init_weights(self):
        super(WindowMSA, self).init_weights()

        trunc_normal_(self.relative_position_bias_table, std=0.02)

    def forward(self, x, mask=None):
        """
        Args:

            x (tensor): input features with shape of (num_windows*B, N, C)
            mask (tensor, Optional): mask with shape of (num_windows, Wh*Ww,
                Wh*Ww), value should be between (-inf, 0].
        """
        B_, N, C = x.shape
        qkv = self.qkv(x).reshape(B_, N, 3, self.num_heads,
                                  C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[
            2]  # make torchscript happy (cannot use tensor as tuple)

        q = q * self.scale
        attn = (q @ k.transpose(-2, -1))

        relative_position_bias = self.relative_position_bias_table[
            self.relative_position_index.view(-1)].view(
                self.window_size[0] * self.window_size[1],
                self.window_size[0] * self.window_size[1],
                -1)  # Wh*Ww,Wh*Ww,nH
        relative_position_bias = relative_position_bias.permute(
            2, 0, 1).contiguous()  # nH, Wh*Ww, Wh*Ww
        attn = attn + relative_position_bias.unsqueeze(0)

        if mask is not None:
            nW = mask.shape[0]
            attn = attn.view(B_ // nW, nW, self.num_heads, N,
                             N) + mask.unsqueeze(1).unsqueeze(0)
            attn = attn.view(-1, self.num_heads, N, N)
            attn = self.softmax(attn)
        else:
            attn = self.softmax(attn)

        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1, 2).reshape(B_, N, C)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x

    @staticmethod
    def double_step_seq(step1, len1, step2, len2):
        seq1 = torch.arange(0, step1 * len1, step1)
        seq2 = torch.arange(0, step2 * len2, step2)
        return (seq1[:, None] + seq2[None, :]).reshape(1, -1)


class ShiftWindowMSA(BaseModule):
    """Shift Window Multihead Self-Attention Module.

    Args:
        embed_dims (int): Number of input channels.
        num_heads (int): Number of attention heads.
        window_size (int): The height and width of the window.
        shift_size (int, optional): The shift step of each window towards
            right-bottom. If zero, act as regular window-msa. Defaults to 0.
        qkv_bias (bool, optional): If True, add a learnable bias to q, k, v.
            Defaults to True
        qk_scale (float | None, optional): Override default qk scale of
            head_dim ** -0.5 if set. Defaults to None.
        attn_drop (float, optional): Dropout ratio of attention weight.
            Defaults to 0.0.
        proj_drop (float, optional): Dropout ratio of output. Defaults to 0.
        dropout_layer (dict, optional): The dropout_layer used before output.
            Defaults to dict(type='DropPath', drop_prob=0.).
        pad_small_map (bool): If True, pad the small feature map to the window
            size, which is common used in detection and segmentation. If False,
            avoid shifting window and shrink the window size to the size of
            feature map, which is common used in classification.
            Defaults to False.
        version (str, optional): Version of implementation of Swin
            Transformers. Defaults to `v1`.
        init_cfg (dict, optional): The extra config for initialization.
            Defaults to None.
    """

    def __init__(self,
                 embed_dims,
                 num_heads,
                 window_size,
                 shift_size=0,
                 qkv_bias=True,
                 qk_scale=None,
                 attn_drop=0,
                 proj_drop=0,
                 dropout_layer=dict(type='DropPath', drop_prob=0.),
                 pad_small_map=False,
                 input_resolution=None,
                 auto_pad=None,
                 window_msa=WindowMSA,
                 msa_cfg=dict(),
                 init_cfg=None):
        super().__init__(init_cfg)

        if input_resolution is not None or auto_pad is not None:
            warnings.warn(
                'The ShiftWindowMSA in new version has supported auto padding '
                'and dynamic input shape in all condition. And the argument '
                '`auto_pad` and `input_resolution` have been deprecated.',
                DeprecationWarning)

        self.shift_size = shift_size
        self.window_size = window_size
        assert 0 <= self.shift_size < self.window_size

        assert issubclass(window_msa, BaseModule), \
            'Expect Window based multi-head self-attention Module is type of' \
            f'{type(BaseModule)}, but got {type(window_msa)}.'
        self.w_msa = window_msa(
            embed_dims=embed_dims,
            window_size=to_2tuple(self.window_size),
            num_heads=num_heads,
            qkv_bias=qkv_bias,
            qk_scale=qk_scale,
            attn_drop=attn_drop,
            proj_drop=proj_drop,
            **msa_cfg,
        )

        self.drop = build_dropout(dropout_layer)
        self.pad_small_map = pad_small_map

    def forward(self, query, hw_shape):
        B, L, C = query.shape
        H, W = hw_shape
        assert L == H * W, f"The query length {L} doesn't match the input "\
            f'shape ({H}, {W}).'
        query = query.view(B, H, W, C)

        window_size = self.window_size
        shift_size = self.shift_size

        if min(H, W) == window_size:
            # If not pad small feature map, avoid shifting when the window size
            # is equal to the size of feature map. It's to align with the
            # behavior of the original implementation.
            shift_size = shift_size if self.pad_small_map else 0
        elif min(H, W) < window_size:
            # In the original implementation, the window size will be shrunk
            # to the size of feature map. The behavior is different with
            # swin-transformer for downstream tasks. To support dynamic input
            # shape, we don't allow this feature.
            assert self.pad_small_map, \
                f'The input shape ({H}, {W}) is smaller than the window ' \
                f'size ({window_size}). Please set `pad_small_map=True`, or ' \
                'decrease the `window_size`.'

        pad_r = (window_size - W % window_size) % window_size
        pad_b = (window_size - H % window_size) % window_size
        query = F.pad(query, (0, 0, 0, pad_r, 0, pad_b))

        H_pad, W_pad = query.shape[1], query.shape[2]

        # cyclic shift
        if shift_size > 0:
            query = torch.roll(
                query, shifts=(-shift_size, -shift_size), dims=(1, 2))

        attn_mask = self.get_attn_mask((H_pad, W_pad),
                                       window_size=window_size,
                                       shift_size=shift_size,
                                       device=query.device)

        # nW*B, window_size, window_size, C
        query_windows = self.window_partition(query, window_size)
        # nW*B, window_size*window_size, C
        query_windows = query_windows.view(-1, window_size**2, C)

        # W-MSA/SW-MSA (nW*B, window_size*window_size, C)
        attn_windows = self.w_msa(query_windows, mask=attn_mask)

        # merge windows
        attn_windows = attn_windows.view(-1, window_size, window_size, C)

        # B H' W' C
        shifted_x = self.window_reverse(attn_windows, H_pad, W_pad,
                                        window_size)
        # reverse cyclic shift
        if self.shift_size > 0:
            x = torch.roll(
                shifted_x, shifts=(shift_size, shift_size), dims=(1, 2))
        else:
            x = shifted_x

        if H != H_pad or W != W_pad:
            x = x[:, :H, :W, :].contiguous()

        x = x.view(B, H * W, C)

        x = self.drop(x)

        return x

    @staticmethod
    def window_reverse(windows, H, W, window_size):
        B = int(windows.shape[0] / (H * W / window_size / window_size))
        x = windows.view(B, H // window_size, W // window_size, window_size,
                         window_size, -1)
        x = x.permute(0, 1, 3, 2, 4, 5).contiguous().view(B, H, W, -1)
        return x

    @staticmethod
    def window_partition(x, window_size):
        B, H, W, C = x.shape
        x = x.view(B, H // window_size, window_size, W // window_size,
                   window_size, C)
        windows = x.permute(0, 1, 3, 2, 4, 5).contiguous()
        windows = windows.view(-1, window_size, window_size, C)
        return windows

    @staticmethod
    def get_attn_mask(hw_shape, window_size, shift_size, device=None):
        if shift_size > 0:
            img_mask = torch.zeros(1, *hw_shape, 1, device=device)
            h_slices = (slice(0, -window_size), slice(-window_size,
                                                      -shift_size),
                        slice(-shift_size, None))
            w_slices = (slice(0, -window_size), slice(-window_size,
                                                      -shift_size),
                        slice(-shift_size, None))
            cnt = 0
            for h in h_slices:
                for w in w_slices:
                    img_mask[:, h, w, :] = cnt
                    cnt += 1

            # nW, window_size, window_size, 1
            mask_windows = ShiftWindowMSA.window_partition(
                img_mask, window_size)
            mask_windows = mask_windows.view(-1, window_size * window_size)
            attn_mask = mask_windows.unsqueeze(1) - mask_windows.unsqueeze(2)
            attn_mask = attn_mask.masked_fill(attn_mask != 0, -100.0)
            attn_mask = attn_mask.masked_fill(attn_mask == 0, 0.0)
        else:
            attn_mask = None
        return attn_mask


class MultiheadAttention(BaseModule):
    """Multi-head Attention Module.

    This module implements multi-head attention that supports different input
    dims and embed dims. And it also supports a shortcut from ``value``, which
    is useful if input dims is not the same with embed dims.

    Args:
        embed_dims (int): The embedding dimension.
        num_heads (int): Parallel attention heads.
        input_dims (int, optional): The input dimension, and if None,
            use ``embed_dims``. Defaults to None.
        attn_drop (float): Dropout rate of the dropout layer after the
            attention calculation of query and key. Defaults to 0.
        proj_drop (float): Dropout rate of the dropout layer after the
            output projection. Defaults to 0.
        dropout_layer (dict): The dropout config before adding the shortcut.
            Defaults to ``dict(type='Dropout', drop_prob=0.)``.
        qkv_bias (bool): If True, add a learnable bias to q, k, v.
            Defaults to True.
        qk_scale (float, optional): Override default qk scale of
            ``head_dim ** -0.5`` if set. Defaults to None.
        proj_bias (bool) If True, add a learnable bias to output projection.
            Defaults to True.
        v_shortcut (bool): Add a shortcut from value to output. It's usually
            used if ``input_dims`` is different from ``embed_dims``.
            Defaults to False.
        init_cfg (dict, optional): The Config for initialization.
            Defaults to None.
    """

    def __init__(self,
                 embed_dims,
                 num_heads,
                 input_dims=None,
                 attn_drop=0.,
                 proj_drop=0.,
                 dropout_layer=dict(type='Dropout', drop_prob=0.),
                 qkv_bias=True,
                 qk_scale=None,
                 proj_bias=True,
                 v_shortcut=False,
                 use_layer_scale=False,
                 init_cfg=None):
        super(MultiheadAttention, self).__init__(init_cfg=init_cfg)

        self.input_dims = input_dims or embed_dims
        self.embed_dims = embed_dims
        self.num_heads = num_heads
        self.v_shortcut = v_shortcut

        self.head_dims = embed_dims // num_heads
        self.scale = qk_scale or self.head_dims**-0.5

        self.qkv = nn.Linear(self.input_dims, embed_dims * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(embed_dims, embed_dims, bias=proj_bias)
        self.proj_drop = nn.Dropout(proj_drop)

        self.out_drop = build_dropout(dropout_layer)

        if use_layer_scale:
            self.gamma1 = LayerScale(embed_dims)
        else:
            self.gamma1 = nn.Identity()

    def forward(self, x):
        B, N, _ = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads,
                                  self.head_dims).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]

        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.softmax(dim=-1)
        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1, 2).reshape(B, N, self.embed_dims)
        x = self.proj(x)
        x = self.out_drop(self.gamma1(self.proj_drop(x)))

        if self.v_shortcut:
            x = v.squeeze(1) + x
        return x

class WindowMSAV2(BaseModule):
    """Window based multi-head self-attention (W-MSA) module with relative
    position bias.

    Based on implementation on Swin Transformer V2 original repo. Refers to
    https://github.com/microsoft/Swin-Transformer/blob/main/models/swin_transformer_v2.py
    for more details.

    Args:
        embed_dims (int): Number of input channels.
        window_size (tuple[int]): The height and width of the window.
        num_heads (int): Number of attention heads.
        qkv_bias (bool, optional): If True, add a learnable bias to q, k, v.
            Defaults to True.
        attn_drop (float, optional): Dropout ratio of attention weight.
            Defaults to 0.
        proj_drop (float, optional): Dropout ratio of output. Defaults to 0.
        pretrained_window_size (tuple(int)): The height and width of the window
            in pre-training.
        init_cfg (dict, optional): The extra config for initialization.
            Defaults to None.
    """

    def __init__(self,
                 embed_dims,
                 window_size,
                 num_heads,
                 qkv_bias=True,
                 attn_drop=0.,
                 proj_drop=0.,
                 cpb_mlp_hidden_dims=512,
                 pretrained_window_size=(0, 0),
                 init_cfg=None,
                 **kwargs):  # accept extra arguments

        super().__init__(init_cfg)
        self.embed_dims = embed_dims
        self.window_size = window_size  # Wh, Ww
        self.num_heads = num_heads

        # Use small network for continuous relative position bias
        self.cpb_mlp = nn.Sequential(
            nn.Linear(
                in_features=2, out_features=cpb_mlp_hidden_dims, bias=True),
            nn.ReLU(inplace=True),
            nn.Linear(
                in_features=cpb_mlp_hidden_dims,
                out_features=num_heads,
                bias=False))

        # Add learnable scalar for cosine attention
        self.logit_scale = nn.Parameter(
            torch.log(10 * torch.ones((num_heads, 1, 1))), requires_grad=True)

        # get relative_coords_table
        relative_coords_h = torch.arange(
            -(self.window_size[0] - 1),
            self.window_size[0],
            dtype=torch.float32)
        relative_coords_w = torch.arange(
            -(self.window_size[1] - 1),
            self.window_size[1],
            dtype=torch.float32)
        relative_coords_table = torch.stack(
            torch.meshgrid([relative_coords_h, relative_coords_w])).permute(
                1, 2, 0).contiguous().unsqueeze(0)  # 1, 2*Wh-1, 2*Ww-1, 2
        if pretrained_window_size[0] > 0:
            relative_coords_table[:, :, :, 0] /= (
                pretrained_window_size[0] - 1)
            relative_coords_table[:, :, :, 1] /= (
                pretrained_window_size[1] - 1)
        else:
            relative_coords_table[:, :, :, 0] /= (self.window_size[0] - 1)
            relative_coords_table[:, :, :, 1] /= (self.window_size[1] - 1)
        relative_coords_table *= 8  # normalize to -8, 8
        relative_coords_table = torch.sign(relative_coords_table) * torch.log2(
            torch.abs(relative_coords_table) + 1.0) / np.log2(8)
        self.register_buffer('relative_coords_table', relative_coords_table)

        # get pair-wise relative position index
        # for each token inside the window
        indexes_h = torch.arange(self.window_size[0])
        indexes_w = torch.arange(self.window_size[1])
        coordinates = torch.stack(
            torch.meshgrid([indexes_h, indexes_w]), dim=0)  # 2, Wh, Ww
        coordinates = torch.flatten(coordinates, start_dim=1)  # 2, Wh*Ww
        # 2, Wh*Ww, Wh*Ww
        relative_coordinates = coordinates[:, :, None] - coordinates[:,
                                                                     None, :]
        relative_coordinates = relative_coordinates.permute(
            1, 2, 0).contiguous()  # Wh*Ww, Wh*Ww, 2

        relative_coordinates[:, :, 0] += self.window_size[
            0] - 1  # shift to start from 0
        relative_coordinates[:, :, 1] += self.window_size[1] - 1
        relative_coordinates[:, :, 0] *= 2 * self.window_size[1] - 1
        relative_position_index = relative_coordinates.sum(-1)  # Wh*Ww, Wh*Ww
        self.register_buffer('relative_position_index',
                             relative_position_index)

        self.qkv = nn.Linear(embed_dims, embed_dims * 3, bias=False)
        if qkv_bias:
            self.q_bias = nn.Parameter(torch.zeros(embed_dims))
            self.v_bias = nn.Parameter(torch.zeros(embed_dims))
        else:
            self.q_bias = None
            self.v_bias = None
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(embed_dims, embed_dims)
        self.proj_drop = nn.Dropout(proj_drop)

        self.softmax = nn.Softmax(dim=-1)

    def forward(self, x, mask=None):
        """
        Args:

            x (tensor): input features with shape of (num_windows*B, N, C)
            mask (tensor, Optional): mask with shape of (num_windows, Wh*Ww,
                Wh*Ww), value should be between (-inf, 0].
        """
        B_, N, C = x.shape
        qkv_bias = None
        if self.q_bias is not None:
            qkv_bias = torch.cat(
                (self.q_bias,
                 torch.zeros_like(self.v_bias,
                                  requires_grad=False), self.v_bias))
        qkv = F.linear(input=x, weight=self.qkv.weight, bias=qkv_bias)
        qkv = qkv.reshape(B_, N, 3, self.num_heads,
                          C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[
            2]  # make torchscript happy (cannot use tensor as tuple)

        # cosine attention
        attn = (
            F.normalize(q, dim=-1) @ F.normalize(k, dim=-1).transpose(-2, -1))
        logit_scale = torch.clamp(
            self.logit_scale, max=np.log(1. / 0.01)).exp()
        attn = attn * logit_scale

        relative_position_bias_table = self.cpb_mlp(
            self.relative_coords_table).view(-1, self.num_heads)
        relative_position_bias = relative_position_bias_table[
            self.relative_position_index.view(-1)].view(
                self.window_size[0] * self.window_size[1],
                self.window_size[0] * self.window_size[1],
                -1)  # Wh*Ww,Wh*Ww,nH
        relative_position_bias = relative_position_bias.permute(
            2, 0, 1).contiguous()  # nH, Wh*Ww, Wh*Ww
        relative_position_bias = 16 * torch.sigmoid(relative_position_bias)
        attn = attn + relative_position_bias.unsqueeze(0)

        if mask is not None:
            nW = mask.shape[0]
            attn = attn.view(B_ // nW, nW, self.num_heads, N,
                             N) + mask.unsqueeze(1).unsqueeze(0)
            attn = attn.view(-1, self.num_heads, N, N)
            attn = self.softmax(attn)
        else:
            attn = self.softmax(attn)

        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1, 2).reshape(B_, N, C)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x
    
class BEiTAttention(BaseModule):
    """Window based multi-head self-attention (W-MSA) module with relative
    position bias.

    The initial implementation is in MMSegmentation.

    Args:
        embed_dims (int): Number of input channels.
        num_heads (int): Number of attention heads.
        window_size (tuple[int]): The height and width of the window.
        use_rel_pos_bias (bool): Whether to use unique relative position bias,
            if False, use shared relative position bias defined in backbone.
        bias (str): The option to add leanable bias for q, k, v. If bias is
            True, it will add leanable bias. If bias is 'qv_bias', it will only
            add leanable bias for q, v. If bias is False, it will not add bias
            for q, k, v. Default to 'qv_bias'.
        qk_scale (float | None, optional): Override default qk scale of
            head_dim ** -0.5 if set. Default: None.
        attn_drop_rate (float): Dropout ratio of attention weight.
            Default: 0.0
        proj_drop_rate (float): Dropout ratio of output. Default: 0.
        init_cfg (dict | None, optional): The Config for initialization.
            Default: None.
    """

    def __init__(self,
                 embed_dims,
                 num_heads,
                 window_size,
                 use_rel_pos_bias,
                 bias='qv_bias',
                 qk_scale=None,
                 attn_drop_rate=0.,
                 proj_drop_rate=0.,
                 init_cfg=None,
                 **kwargs):
        super().__init__(init_cfg=init_cfg)
        self.embed_dims = embed_dims
        self.num_heads = num_heads
        head_embed_dims = embed_dims // num_heads
        self.bias = bias
        self.scale = qk_scale or head_embed_dims**-0.5

        qkv_bias = bias
        if bias == 'qv_bias':
            self._init_qv_bias()
            qkv_bias = False

        self.window_size = window_size
        self.use_rel_pos_bias = use_rel_pos_bias
        self._init_rel_pos_embedding()

        self.qkv = nn.Linear(embed_dims, embed_dims * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop_rate)
        self.proj = nn.Linear(embed_dims, embed_dims)
        self.proj_drop = nn.Dropout(proj_drop_rate)

    def _init_qv_bias(self):
        self.q_bias = nn.Parameter(torch.zeros(self.embed_dims))
        self.v_bias = nn.Parameter(torch.zeros(self.embed_dims))

    def _init_rel_pos_embedding(self):
        if self.use_rel_pos_bias:
            Wh, Ww = self.window_size
            # cls to token & token 2 cls & cls to cls
            self.num_relative_distance = (2 * Wh - 1) * (2 * Ww - 1) + 3
            # relative_position_bias_table shape is (2*Wh-1 * 2*Ww-1 + 3, nH)
            self.relative_position_bias_table = nn.Parameter(
                torch.zeros(self.num_relative_distance, self.num_heads))

            # get pair-wise relative position index for
            # each token inside the window
            coords_h = torch.arange(Wh)
            coords_w = torch.arange(Ww)
            # coords shape is (2, Wh, Ww)
            coords = torch.stack(torch_meshgrid([coords_h, coords_w]))
            # coords_flatten shape is (2, Wh*Ww)
            coords_flatten = torch.flatten(coords, 1)
            relative_coords = (
                coords_flatten[:, :, None] - coords_flatten[:, None, :])
            # relative_coords shape is (Wh*Ww, Wh*Ww, 2)
            relative_coords = relative_coords.permute(1, 2, 0).contiguous()
            # shift to start from 0
            relative_coords[:, :, 0] += Wh - 1
            relative_coords[:, :, 1] += Ww - 1
            relative_coords[:, :, 0] *= 2 * Ww - 1
            relative_position_index = torch.zeros(
                size=(Wh * Ww + 1, ) * 2, dtype=relative_coords.dtype)
            # relative_position_index shape is (Wh*Ww, Wh*Ww)
            relative_position_index[1:, 1:] = relative_coords.sum(-1)
            relative_position_index[0, 0:] = self.num_relative_distance - 3
            relative_position_index[0:, 0] = self.num_relative_distance - 2
            relative_position_index[0, 0] = self.num_relative_distance - 1

            self.register_buffer('relative_position_index',
                                 relative_position_index)
        else:
            self.window_size = None
            self.relative_position_bias_table = None
            self.relative_position_index = None

    def init_weights(self):
        super().init_weights()
        if self.use_rel_pos_bias:
            trunc_normal_(self.relative_position_bias_table, std=0.02)

    def forward(self, x, rel_pos_bias=None):
        """
        Args:
            x (tensor): input features with shape of (num_windows*B, N, C).
            rel_pos_bias (tensor): input relative position bias with shape of
                (num_heads, N, N).
        """
        B, N, C = x.shape

        if self.bias == 'qv_bias':
            k_bias = torch.zeros_like(self.v_bias, requires_grad=False)
            qkv_bias = torch.cat((self.q_bias, k_bias, self.v_bias))
            qkv = F.linear(input=x, weight=self.qkv.weight, bias=qkv_bias)
        else:
            qkv = self.qkv(x)

        qkv = qkv.reshape(B, N, 3, self.num_heads, -1).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]
        q = q * self.scale
        attn = (q @ k.transpose(-2, -1))
        if self.relative_position_bias_table is not None:
            Wh = self.window_size[0]
            Ww = self.window_size[1]
            relative_position_bias = self.relative_position_bias_table[
                self.relative_position_index.view(-1)].view(
                    Wh * Ww + 1, Wh * Ww + 1, -1)
            relative_position_bias = relative_position_bias.permute(
                2, 0, 1).contiguous()  # nH, Wh*Ww, Wh*Ww
            attn = attn + relative_position_bias.unsqueeze(0)

        if rel_pos_bias is not None:
            # use shared relative position bias
            attn = attn + rel_pos_bias

        attn = attn.softmax(dim=-1)
        attn = self.attn_drop(attn)
        x = (attn @ v).transpose(1, 2).reshape(B, N, C)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x
    
class LeAttention(BaseModule):
    """LeViT Attention. Multi-head attention with attention bias,  which is
    proposed in `LeViT: a Vision Transformer in ConvNet’s Clothing for Faster
    Inference<https://arxiv.org/abs/2104.01136>`_

    Args:
        dim (int): Number of input channels.
        num_heads (int): Number of attention heads. Default: 8.
        key_dim (int): Dimension of key. Default: None.
        attn_ratio (int): Ratio of attention heads. Default: 8.
        resolution (tuple[int]): Input resolution. Default: (16, 16).
        init_cfg (dict, optional): The Config for initialization.
    """

    def __init__(self,
                 dim,
                 key_dim,
                 num_heads=8,
                 attn_ratio=4,
                 resolution=(14, 14),
                 init_cfg=None):
        super().__init__(init_cfg=init_cfg)
        # (h, w)
        assert isinstance(resolution, tuple) and len(resolution) == 2
        self.num_heads = num_heads
        self.scale = key_dim**-0.5
        self.key_dim = key_dim
        self.nh_kd = nh_kd = key_dim * num_heads
        self.d = int(attn_ratio * key_dim)
        self.dh = int(attn_ratio * key_dim) * num_heads
        self.attn_ratio = attn_ratio
        h = self.dh + nh_kd * 2

        self.norm = nn.LayerNorm(dim)
        self.qkv = nn.Linear(dim, h)
        self.proj = nn.Linear(self.dh, dim)

        points = list(
            itertools.product(range(resolution[0]), range(resolution[1])))
        N = len(points)
        attention_offsets = {}
        idxs = []
        for p1 in points:
            for p2 in points:
                offset = (abs(p1[0] - p2[0]), abs(p1[1] - p2[1]))
                if offset not in attention_offsets:
                    attention_offsets[offset] = len(attention_offsets)
                idxs.append(attention_offsets[offset])
        self.attention_biases = torch.nn.Parameter(
            torch.zeros(num_heads, len(attention_offsets)))
        self.register_buffer(
            'attention_bias_idxs',
            torch.LongTensor(idxs).view(N, N),
            persistent=False)

    @torch.no_grad()
    def train(self, mode=True):
        super().train(mode)
        if mode and hasattr(self, 'ab'):
            del self.ab
        else:
            self.ab = self.attention_biases[:, self.attention_bias_idxs]

    def forward(self, x):  # x (B,N,C)
        B, N, _ = x.shape

        # Normalization
        x = self.norm(x)

        qkv = self.qkv(x)
        # (B, N, num_heads, d)
        q, k, v = qkv.view(B, N, self.num_heads,
                           -1).split([self.key_dim, self.key_dim, self.d],
                                     dim=3)
        # (B, num_heads, N, d)
        q = q.permute(0, 2, 1, 3)
        k = k.permute(0, 2, 1, 3)
        v = v.permute(0, 2, 1, 3)

        attn = ((q @ k.transpose(-2, -1)) * self.scale +
                (self.attention_biases[:, self.attention_bias_idxs]
                 if self.training else self.ab))
        attn = attn.softmax(dim=-1)
        x = (attn @ v).transpose(1, 2).reshape(B, N, self.dh)
        x = self.proj(x)
        return x
    
class ChannelMultiheadAttention(BaseModule):
    """Channel Multihead Self-attention Module.

    This module implements channel multi-head attention that supports different
    input dims and embed dims.
    Args:
        embed_dims (int): The embedding dimension.
        num_heads (int): Parallel attention heads.
        input_dims (int, optional): The input dimension, and if None,
            use ``embed_dims``. Defaults to None.
        attn_drop (float): Dropout rate of the dropout layer after the
            attention calculation of query and key. Defaults to 0.
        proj_drop (float): Dropout rate of the dropout layer after the
            output projection. Defaults to 0.
        dropout_layer (dict): The dropout config before adding the shoutcut.
            Defaults to ``dict(type='Dropout', drop_prob=0.)``.
        qkv_bias (bool): If True, add a learnable bias to q, k, v.
            Defaults to False.
        proj_bias (bool) If True, add a learnable bias to output projection.
            Defaults to True.
        qk_scale_type (str): The scale type of qk scale.
            Defaults to 'learnable'. It can be 'learnable', 'fixed' or 'none'.
        qk_scale (float, optional): If set qk_scale_type to 'none', this
            should be specified with valid float number. Defaults to None.
        v_shortcut (bool): Add a shortcut from value to output. It's usually
            used if ``input_dims`` is different from ``embed_dims``.
            Defaults to False.
        init_cfg (dict, optional): The Config for initialization.
            Defaults to None.
    """

    def __init__(self,
                 embed_dims,
                 num_heads=8,
                 input_dims=None,
                 attn_drop=0.,
                 proj_drop=0.,
                 dropout_layer=dict(type='Dropout', drop_prob=0.),
                 qkv_bias=False,
                 proj_bias=True,
                 qk_scale_type='learnable',
                 qk_scale=None,
                 v_shortcut=False,
                 init_cfg=None):
        super().__init__(init_cfg)

        self.input_dims = input_dims or embed_dims
        self.embed_dims = embed_dims
        self.num_heads = num_heads
        self.v_shortcut = v_shortcut

        self.head_dims = embed_dims // num_heads
        if qk_scale_type == 'learnable':
            self.scale = nn.Parameter(torch.ones(num_heads, 1, 1))
        elif qk_scale_type == 'fixed':
            self.scale = self.head_dims**-0.5
        elif qk_scale_type == 'none':
            assert qk_scale is not None
            self.scale = qk_scale

        self.qkv = nn.Linear(self.input_dims, embed_dims * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(embed_dims, embed_dims, bias=proj_bias)
        self.proj_drop = nn.Dropout(proj_drop)

        self.out_drop = build_dropout(dropout_layer)

    def forward(self, x):
        B, N, _ = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads,
                                  self.head_dims).permute(2, 0, 3, 1, 4)

        q, k, v = [item.transpose(-2, -1) for item in [qkv[0], qkv[1], qkv[2]]]

        q, k = F.normalize(q, dim=-1), F.normalize(k, dim=-1)

        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.softmax(dim=-1)

        x = (attn @ v).permute(0, 3, 1, 2).reshape(B, N, self.embed_dims)
        x = self.proj(x)
        x = self.out_drop(self.proj_drop(x))

        if self.v_shortcut:
            x = qkv[2].squeeze(1) + x
        return x