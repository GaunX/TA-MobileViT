#提取目录下所有图片,更改尺寸后保存到另一目录
from PIL import Image
import os.path
import glob
def convertjpg(jpgfile,outdir,width=224,height=224):
    img=Image.open(jpgfile)
    try:
        new_img=img.resize((width,height),Image.BILINEAR)   
        new_img.save(os.path.join(outdir,os.path.basename(jpgfile)))
    except Exception as e:
        print(e)
folders = {
    r"E:\HT_png\2023\wave-png-origion\AES-T800+TrojanTriggered_1" : r"E:\HT_png\2023\wave-png-224x224\AES-T800\Triggered",
    r"E:\HT_png\2023\wave-png-origion\AES-T1600+TrojanDisabled_1" : r"E:\HT_png\2023\wave-png-224x224\AES-T1600\Disabled",
    r"E:\HT_png\2023\wave-png-origion\AES-T1600+TrojanTriggered_1" : r"E:\HT_png\2023\wave-png-224x224\AES-T1600\Triggered"
}
for png_folder,output_folder in folders.items():
    print(f"Processing folder: {png_folder} to {output_folder}")
    for pngfile in glob.glob(os.path.join(png_folder,'*.png')):
        convertjpg(pngfile,output_folder)
print("Covered Over!")
# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T500+TrojanDisabled_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T500\Disabled"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T500+TrojanTriggered_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T500\Triggered"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T600+TrojanDisabled_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T600\Disabled"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T600+TrojanTriggered_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T600\Triggered"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T700+TrojanDisabled_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T700\Disabled"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T700+TrojanTriggered_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T700\Triggered"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T800+TrojanDisabled_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T800\Disabled"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T800+TrojanTriggered_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T800\Triggered"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T1600+TrojanDisabled_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T1600\Disabled"

# png_folder = r"E:\HT_png\2023\wave-png-origion\AES-T1600+TrojanTriggered_1"
# output_folder = r"E:\HT_png\2023\wave-png-224x224\AES-T1600\Triggered"
#############checkout####################
# from PIL import Image
# import os.path
# infile = 'F:/itti17/1.png'
# infile_1 ='F:/itti/1.png'
# im = Image.open(infile)
# im_1 = Image.open(infile_1)
# (x, y) = im.size  # read image size
# (x1,y1) = im_1.size
# print ('original size: ', x, y)
# print ('current size: ', x1, y1)
##########输出原始尺寸与转换后的尺寸##########