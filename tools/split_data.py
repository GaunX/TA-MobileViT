import os
from shutil import copy, rmtree
import random
from tqdm import tqdm


def split_data(init_dataset,new_dataset):
    '''
    split_rates  : Proportion of training set, verification set and test set
    init_dataset: Data set path before partition
    new_dataset : The partitioned data set path

    '''

    def makedir(path):
        if os.path.exists(path):
            rmtree(path)
        os.makedirs(path)

    split_rates = [0.6, 0.2, 0.2]
    assert sum(split_rates) == 1
    random.seed(0)

    classes_name = [name for name in os.listdir(init_dataset)]

    makedir(new_dataset)
    training_set = os.path.join(new_dataset, "train")
    validation_set = os.path.join(new_dataset, "val")
    test_set = os.path.join(new_dataset, "test")
    makedir(training_set)
    makedir(validation_set)
    makedir(test_set)

    for cla in classes_name:
        makedir(os.path.join(training_set, cla))
        makedir(os.path.join(validation_set, cla))
        makedir(os.path.join(test_set, cla))

    for cla in classes_name:
        class_path = os.path.join(init_dataset, cla)
        # img_set = os.listdir(class_path)  #原始代码，未过滤文件类型
        # 修改代码，筛选文件类型，只要图片类型
        img_set = [img for img in os.listdir(class_path) if img.endswith(('.png', '.jpg', '.jpeg'))]
        num = len(img_set)
        train_count = int(num * split_rates[0])
        val_count = int(num * split_rates[1])
        train_set_index = random.sample(img_set, k=train_count)
        remaining_set = [img for img in img_set if img not in train_set_index]
        val_set_index = random.sample(remaining_set, k=val_count)

        with tqdm(total=num, desc=f'Class: ' + cla, mininterval=0.3) as pbar:
            for img in img_set:
                init_img = os.path.join(class_path, img)
                if img in train_set_index:
                    new_img = os.path.join(training_set, cla)
                elif img in val_set_index:
                    new_img = os.path.join(validation_set, cla)
                else:
                    new_img = os.path.join(test_set, cla)
                copy(init_img, new_img)
                pbar.update(1)
        print()

# init : new
folders = {
    r"" : r""

}
for init_dataset,new_dataset in folders.items():
    print(f"{init_dataset} is splited to {new_dataset}" )
    split_data(init_dataset,new_dataset)
print("All datasets have be split successfully")
