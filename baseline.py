# -----------------------------------------
# THIS FILE WAS AUTOGENERATED! DO NOT EDIT!
# -----------------------------------------
# file to edit: 01_baseline.ipynb

from collections import OrderedDict
import os
import re
from pdb import set_trace
from multiprocessing import cpu_count
from pprint import pprint as pp
import warnings
warnings.filterwarnings('ignore')

from imageio import imread
import numpy as np
import pandas as pd
import PIL.Image

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
import torchvision
import torchvision.transforms as T

from catalyst.contrib.schedulers import OneCycleLR
from catalyst.data.dataset import ListDataset
from catalyst.dl.callbacks import AccuracyCallback, AUCCallback, F1ScoreCallback
from catalyst.dl.runner import SupervisedRunner
import pretrainedmodels
from jupytools import auto_set_trace


seed = 1
set_trace = auto_set_trace()
os.environ['CUDA_VISIBLE_DEVICES'] = '1'


def list_files(folder):
    dirname = os.path.expanduser(folder)
    return [os.path.join(dirname, x) for x in os.listdir(dirname)]


def extract_labels(files):
    regex = re.compile('.*_(\d+)\\.png$')
    return [int(regex.match(os.path.basename(fn)).group(1)) for fn in files]


uniq_labels = np.unique(extract_labels(list_files('~/data/protein/tmp/train')))
num_classes = len(uniq_labels)


from typing import List, Dict, Callable, Any

class PathsDataset(ListDataset):
    """
    Dataset that derives features and targets from samples filesystem paths.
    """
    def __init__(
        self,
        filenames: List[Dict],
        open_fn: Callable,
        get_label_fn: Callable,
        **list_dataset_params
    ):
        list_data = [
            {'features': filename,
             'targets': get_label_fn(filename)}
            for filename in filenames]

        super().__init__(
            list_data=list_data,
            open_fn=open_fn,
            **list_dataset_params
        )

class RegexLabelExtractor:
    def __init__(self, regex):
        self.regex = re.compile(regex)

    def __call__(self, filename):
        return int(self.regex.match(os.path.basename(filename)).group(1))

class ImageTransformer:
    def __init__(self, image_tr: Callable):
        self.image_tr = image_tr

    def __call__(self, dict_):
        dict_ = dict_.copy()
        dict_['features'] = self.image_tr(dict_['features'])
        return dict_

class OneHotTransformer:
    def __init__(self, num_classes):
        self.num_classes = num_classes

    def __call__(self, dict_):
        y = dict_['targets']
        onehot = np.zeros(self.num_classes, dtype=np.float32)
        onehot[y] = 1
        dict_['targets_one_hot'] = onehot
        return dict_

class TransformerList:
    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, dict_):
        from functools import reduce
        return reduce(lambda x, f: f(x), self.transforms, dict_)

def open_image(dict_):
    dict_ = dict_.copy()
    dict_['features'] = PIL.Image.open(dict_['features'])
    return dict_

model_name = 'resnet50'
params = pretrainedmodels.pretrained_settings[model_name]['imagenet']
pp(params)

transformers = TransformerList([
    ImageTransformer(T.Compose([
        T.Resize((224, 224)),
        T.ToTensor(),
        T.Normalize(params['mean'], params['std'])
    ])),
    OneHotTransformer(num_classes)
])

labelled_files = list_files('~/data/protein/tmp/train')

from sklearn.model_selection import train_test_split
trn_files, tst_files = train_test_split(labelled_files, test_size=0.1, random_state=seed)
regex_label = RegexLabelExtractor('.*_(\d+)\\.png$')

trn_ds = PathsDataset(
    filenames=trn_files,
    open_fn=open_image,
    get_label_fn=regex_label,
    dict_transform=transformers
)

val_ds = PathsDataset(
    filenames=tst_files,
    open_fn=open_image,
    get_label_fn=regex_label,
    dict_transform=transformers
)

batch_size = 800
loaders = OrderedDict()
loaders['train'] = DataLoader(trn_ds, shuffle=True, batch_size=batch_size)
loaders['valid'] = DataLoader(val_ds, shuffle=False, batch_size=batch_size)


def get_model(model_name, num_classes, pretrained='imagenet'):
    model_fn = pretrainedmodels.__dict__[model_name]
    model = model_fn(num_classes=1000, pretrained=pretrained)
    dim_feats = model.last_linear.in_features
    model.last_linear = nn.Linear(dim_feats, num_classes)
    return model


epochs = 30

resnet = get_model(model_name, num_classes)
for param in resnet.parameters():
    param.requires_grad = False

resnet.last_linear.weight.requires_grad = True
for param in resnet.layer4.parameters():
    param.requires_grad = True

loss_fn = nn.CrossEntropyLoss()
opt = torch.optim.SGD(resnet.parameters(), lr=0.01, momentum=0.9)
logdir = '/tmp/protein/logs/'
runner = SupervisedRunner()
sched = OneCycleLR(opt,
                   num_steps=epochs * len(loaders['train']),
                   warmup_fraction=0.3,
                   lr_range=(0.1, 0.0001))

runner.train(
    model=resnet,
    criterion=loss_fn,
    optimizer=opt,
    loaders=loaders,
    logdir=logdir,
    num_epochs=epochs,
    scheduler=sched,
    callbacks=[
        AccuracyCallback(num_classes=num_classes),
        F1ScoreCallback(
            input_key="targets_one_hot",
            activation="Softmax"
        )
    ],
    verbose=True
)

print('Saving the trained model')
basedir = os.path.expanduser('~/data/protein/tmp/models')
os.makedirs(basedir, exist_ok=True)
torch.save(resnet, os.path.join(basedir, 'resnet50_simple.pth'))
