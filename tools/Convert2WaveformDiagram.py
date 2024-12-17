import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import os

# 指定存储CSV文件的父文件夹
parent_folder = 'E:\HT'

# 获取所有子文件夹
subfolders = [ r'E:\test'
              ]

# 遍历每个子文件夹
for subfolder in subfolders:
    # 构建CSV文件夹的完整路径
    csv_folder = os.path.join(parent_folder, subfolder)

    # 创建与CSV文件夹同名的输出文件夹
    output_folder = os.path.join('./png/', os.path.basename(csv_folder))
    os.makedirs(output_folder, exist_ok=True)

    # 获取文件夹中的所有CSV文件
    csv_files = [f for f in os.listdir(csv_folder) if f.endswith('.csv')]

    for csv_file in csv_files:
        # 构建CSV文件的完整路径
        csv_file_path = os.path.join(csv_folder, csv_file)

        # 读取CSV文件数据
        data = pd.read_csv(csv_file_path).to_numpy()

        # 遍历每一列并生成波形图
        for i in range(data.shape[1]):
            plt.plot(data[:, i])
            plt.axis('off')

            # 保存图像为PNG文件
            output_filename = '{}_{}.png'.format(os.path.splitext(csv_file)[0], i)
            output_path = os.path.join(output_folder, output_filename)
            plt.savefig(output_path, bbox_inches='tight', pad_inches=0)

            plt.close()

print("Processing complete.")



# import matplotlib.pyplot as plt
# import csv
#
# data = ([])
#
# with open('AES-T2000+TrojanDisabled_1.csv', 'rt') as csvfile:
#     reader = csv.reader(csvfile)
#     for i in range(0, 10):
#         print(i)
#         for row in reader:
#             data.append(int(row[i]))
#         plt.figure(figsize=(18, 9))
#         plt.axis('off')   # 去坐标轴
#         plt.xticks([])    # 去 x 轴刻度
#         plt.yticks([])    # 去 y 轴刻度
#         plt.plot(data, 'b', lw=1)
#         plt.savefig('./pic/'+str(i)+'.jpg', bbox_inches='tight', pad_inches=0)
#         plt.show()



# import matplotlib.pyplot as plt
# import numpy as np
# import pandas as pd
# import csv
# import os
# data = pd.read_csv('AES-T2000+TrojanEnabled_1.csv').to_numpy()
# for i in range(0, 10000, 1):
#     plt.plot(data[:, i])
#     plt.axis('off')
#     plt.xticks([])  # 去掉横坐标值
#     plt.yticks([])  # 去掉纵坐标值
#
#     plt.draw()
#     plt.savefig('./file_fig/AES-T2000+TrojanEnabled_1_{}.png'.format(i), bbox_inches='tight', pad_inches=0)
#     #plt.pause(1)
#     print(i)
#     plt.close()
#
# import matplotlib.pyplot as plt
# import numpy as np
# import pandas as pd
# import os

# # 指定存储CSV文件的文件夹
# csv_folder = r'E:\HT\AES-T400_power_Temp25C\AES-T400_power_Temp25C\AES-T400+TrojanTriggered_2\AES-T400+TrojanTriggered_2'
#
# # 获取文件夹中的所有CSV文件
# csv_files = [f for f in os.listdir(csv_folder) if f.endswith('.csv')]
#
# for csv_file in csv_files:
#     # 构建CSV文件的完整路径
#     csv_file_path = os.path.join(csv_folder, csv_file)
#
#     # 读取CSV文件数据
#     data = pd.read_csv(csv_file_path).to_numpy()
#
#     # 遍历每一列并生成波形图
#     for i in range(data.shape[1]):
#         plt.plot(data[:, i])
#         plt.axis('off')
#         plt.xticks([])  # 去掉横坐标值
#         plt.yticks([])  # 去掉纵坐标值
#
#         # 保存图像为PNG文件
#         output_filename = '{}_{}.png'.format(os.path.splitext(csv_file)[0], i)
#         output_path = os.path.join('./AES-T400_power_Temp25C/AES-T400+TrojanTriggered_2', output_filename)
#         plt.savefig(output_path, bbox_inches='tight', pad_inches=0)
#
#         plt.close()
#
# print("Processing complete.")
