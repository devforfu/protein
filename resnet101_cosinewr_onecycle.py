# -----------------------------------------
# THIS FILE WAS AUTOGENERATED! DO NOT EDIT!
# -----------------------------------------
# file to edit: 15_tricks.ipynb

from collections import OrderedDict
import json
import os
from os.path import dirname, join
from functools import reduce
from pdb import set_trace

import cv2 as cv
from imageio import imread
import jupytools
import jupytools.syspath
import numpy as np
import pandas as pd
import PIL.Image
import matplotlib.pyplot as plt

from apex import amp
from catalyst.utils import get_one_hot
import pretrainedmodels
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from visdom import Visdom

jupytools.syspath.add(join(dirname(os.getcwd()), 'protein_project'))
jupytools.syspath.add('rxrx1-utils')
if jupytools.is_notebook():
    from tqdm import tqdm_notebook as tqdm
else:
    from tqdm import tqdm as tdqm

from basedir import ROOT, NUM_CLASSES
from dataset import build_stats_index


torch.set_default_tensor_type(torch.FloatTensor)


from augmentation import JoinChannels, SwapChannels, Resize, ToFloat, Rescale
from augmentation import VerticalFlip, HorizontalFlip, PixelStatsNorm, composer
from augmentation import AugmentedImages, bernoulli


default_open_fn = imread  # PIL.Image.open


class RxRxImages(Dataset):
    def __init__(self, meta_df, img_dir, site=1, channels=(1, 2, 3, 4, 5, 6),
                 open_image=default_open_fn, n_classes=NUM_CLASSES,
                 train=True, smoothing=0.0):

        self.records = meta_df.to_records(index=False)
        self.img_dir = img_dir
        self.site = site
        self.channels = channels
        self.n = len(self.records)
        self.open_image = open_image
        self.n_classes = n_classes
        self.train = train
        self.smoothing = smoothing

    def _get_image_path(self, index, channel):
        r = self.records[index]
        exp, plate, well = r.experiment, r.plate, r.well
        subdir = 'train' if self.train else 'test'
        path = f'{self.img_dir}/{subdir}/{exp}/Plate{plate}/{well}_s{self.site}_w{channel}.png'
        return path

    def __getitem__(self, index):
        paths = [self._get_image_path(index, ch) for ch in self.channels]
        images = [self.open_image(p) for p in paths]

        try:
            img = np.stack(images)
        except (TypeError, ValueError) as e:
            print(f'Warning: cannot concatenate images! {e.__class__.__name__}: {e}')
            for filename, image in zip(paths, images):
                print(f'\tpath={filename}, size={image.size}')
            index = (index + 1) % len(self)
            print(f'Skipping instance {index} and trying another one...')
            return self[index]
        finally:
            for image in images:
                if hasattr(image, 'close'):
                    image.close()

        img = img.astype(np.float32)
        img = img.transpose(1, 2, 0)
        r = self.records[index]
        if self.train:
            sirna = r.sirna
            target = int(sirna)
            onehot = get_one_hot(target, num_classes=self.n_classes, smoothing=self.smoothing)
            return {'features': img, 'targets': target,
                    'targets_one_hot': onehot, 'id_code': r.id_code,
                    'site': self.site}
        else:
            id_code = r.id_code
            return {'features': img, 'id_code': id_code, 'site': self.site}

    def __len__(self):
        return self.n


class TwoSiteImages(Dataset):
    def __init__(self, ds1, ds2, swap=0.0):
        assert len(ds1) == len(ds2)
        self.ds1, self.ds2 = ds1, ds2
        self.swap = swap
        self.size = len(ds1)

    def __getitem__(self, index):
        s1, s2 = self.ds1[index], self.ds2[index]
        if self.swap and bernoulli(self.swap) == 1:
            s1, s2 = s2, s1
        return {'site1': s1, 'site2': s2}

    def __len__(self):
        return self.size


from split import StratifiedSplit
splitter = StratifiedSplit()
trn_df, val_df = splitter(pd.read_csv(ROOT/'train.csv'))
tst_df = pd.read_csv(ROOT/'test.csv')
stats = build_stats_index(ROOT/'pixel_stats.csv')


sz = 512
smoothing = 0.1
trn_ds = TwoSiteImages(
    ds1=AugmentedImages(ds=RxRxImages(trn_df, ROOT, site=1, smoothing=smoothing), tr=composer([
        HorizontalFlip(p=0.1),
        VerticalFlip(p=0.1),
        PixelStatsNorm(stats, channels_first=False),
    ], resize=sz, rescale=False)),
    ds2=AugmentedImages(ds=RxRxImages(trn_df, ROOT, site=2, smoothing=smoothing), tr=composer([
        HorizontalFlip(p=0.1),
        VerticalFlip(p=0.1),
        PixelStatsNorm(stats, channels_first=False),
    ], resize=sz, rescale=False))
)
val_ds = TwoSiteImages(
    ds1=AugmentedImages(ds=RxRxImages(val_df, ROOT, site=1, smoothing=smoothing), tr=composer([
        PixelStatsNorm(stats, channels_first=False)
    ],resize=sz, rescale=False)),
    ds2=AugmentedImages(ds=RxRxImages(val_df, ROOT, site=2, smoothing=smoothing), tr=composer([
        PixelStatsNorm(stats, channels_first=False)
    ],resize=sz, rescale=False))
)

### !!! MAKE SURE THAT THE TEST DATASET IS PREPROCESSED IN THE SAME WAY !!! ###

tst_ds = TwoSiteImages(
    ds1=AugmentedImages(ds=RxRxImages(tst_df, ROOT, site=1, train=False), tr=composer([
        PixelStatsNorm(stats, channels_first=False)
    ], resize=sz, rescale=False)),
    ds2=AugmentedImages(ds=RxRxImages(tst_df, ROOT, site=2, train=False), tr=composer([
        PixelStatsNorm(stats, channels_first=False)
    ], resize=sz, rescale=False))
)


def new_loader(ds, bs, drop_last=False, shuffle=True, num_workers=12):
    return DataLoader(ds, batch_size=bs, drop_last=drop_last,
                      shuffle=shuffle, num_workers=num_workers)


def densenet(name='densenet121', n_classes=NUM_CLASSES):
    model_fn = pretrainedmodels.__dict__[name]
    model = model_fn(num_classes=1000, pretrained='imagenet')
    new_conv = nn.Conv2d(6, 64, 7, 2, 3, bias=False)
    conv0 = model.features.conv0.weight
    with torch.no_grad():
        new_conv.weight[:, :] = torch.stack([torch.mean(conv0, 1)]*6, dim=1)
    model.features.conv0 = new_conv
    return model


def resnet(name='resnet18', n_classes=NUM_CLASSES):
    model_fn = pretrainedmodels.__dict__[name]
    model = model_fn(num_classes=1000, pretrained='imagenet')
    new_conv = nn.Conv2d(6, 64, 7, 2, 3, bias=False)
    conv1 = model.conv1.weight
    with torch.no_grad():
        new_conv.weight[:, :] = torch.stack([torch.mean(conv1, 1)]*6, dim=1)
    model.conv1 = new_conv
    return model


from catalyst.contrib.modules import GlobalConcatPool2d
class TwoSitesModel(nn.Module):
    def __init__(self, backbone_fn, name, n_classes=NUM_CLASSES):
        super().__init__()

        base = backbone_fn(name=name, n_classes=n_classes)
        feat_dim = base.last_linear.in_features
        n = feat_dim * 2

        self.base = base
        self.pool = GlobalConcatPool2d()
        self.head = nn.Sequential(
            nn.Linear(n, n),
            nn.BatchNorm1d(n),
            nn.ReLU(inplace=True),
            nn.Dropout(0.25),
            nn.Linear(n, n),
            nn.BatchNorm1d(n),
            nn.ReLU(inplace=True),
            nn.Dropout(0.25),
            nn.Linear(n, n_classes)
        )

    def forward(self, s1, s2):
        f1 = self.base.features(s1)
        f2 = self.base.features(s2)
        f_merged = self.pool(f1 + f2)
        out = self.head(f_merged.squeeze())
        return out


def freeze_all(model):
    for name, child in model.named_children():
        print('Freezing layer:', name)
        for param in child.parameters():
            param.requires_grad = False


def unfreeze_all(model):
    for name, child in model.named_children():
        print('Un-freezing layer:', name)
        for param in child.parameters():
            param.requires_grad = True


def unfreeze_layers(model, names):
    for name, child in model.named_children():
        if name not in names:
            continue
        print('Un-freezing layer:', name)
        for param in child.parameters():
            param.requires_grad = True


class Checkpoint:
    def __init__(self, output_dir):
        if os.path.exists(output_dir):
            print('Warning! Output folder already exists.')
        os.makedirs(output_dir, exist_ok=True)
        self.output_dir = output_dir

    def __call__(self, epoch, **objects):
        filename = os.path.join(self.output_dir, f'train.{epoch}.pth')
        checkpoint = {}
        for k, v in objects.items():
            if hasattr(v, 'state_dict'):
                v = v.state_dict()
            checkpoint[k] = v
        torch.save(checkpoint, filename)
        return filename


from torch.optim.lr_scheduler import _LRScheduler
class CosineDecay(_LRScheduler):
    def __init__(self, optimizer, total_steps,
                 linear_start=0,
                 linear_frac=0.1, min_lr=1e-6,
                 last_epoch=-1):

        self.optimizer = optimizer
        self.total_steps = total_steps
        self.linear_start = linear_start
        self.linear_frac = linear_frac
        self.min_lr = min_lr
        self.linear_steps = total_steps * linear_frac
        self.cosine_steps = total_steps - self.linear_steps
        super().__init__(optimizer, last_epoch)

    def get_lr(self):
        step = self.last_epoch
        if step <= self.linear_steps:
            b = self.linear_start
            return [(step/self.linear_steps) * (base_lr - b) + b for base_lr in self.base_lrs]
        else:
            t = self.last_epoch - self.linear_steps
            T = self.cosine_steps
            return [self.min_lr + (base_lr - self.min_lr)*(1 + np.cos(t*np.pi/T))/2
                    for base_lr in self.base_lrs]


class LabelSmoothingLoss(nn.Module):
    def __init__(self, dim=-1):
        super().__init__()
        self.dim = dim

    def forward(self, preds, one_hot_target):
        preds = preds.log_softmax(dim=self.dim)
        return torch.mean(torch.sum(-one_hot_target * preds, dim=self.dim))


model = TwoSitesModel(backbone_fn=resnet, name='resnet101')
freeze_all(model)


from visdom import Visdom


class RollingLoss:
    def __init__(self, smooth=0.98):
        self.smooth = smooth
        self.prev = 0
    def __call__(self, curr, batch_no):
        a = self.smooth
        avg_loss = a*self.prev + (1 - a)*curr
        debias_loss = avg_loss/(1 - a**batch_no)
        self.prev = avg_loss
        return debias_loss


def create_loaders(batch_size):
    trn_dl = new_loader(trn_ds, bs=batch_size, shuffle=True)
    val_dl = new_loader(val_ds, bs=batch_size, shuffle=False)
    return OrderedDict([('train', trn_dl), ('valid', val_dl)])


loss_fn = LabelSmoothingLoss()
device = torch.device('cuda:0')


max_lr = 3e-4
model = model.to(device)
opt = torch.optim.AdamW(model.parameters(), lr=max_lr)
opt_level = 'O1'
model, opt = amp.initialize(model, opt, opt_level=opt_level, num_losses=1)
rolling_loss = dict(train=RollingLoss(), valid=RollingLoss())
steps = dict(train=0, valid=0)

log_freq = 25
trials = 0
best_metric = -np.inf
history = []
stop = False

vis = Visdom(server='0.0.0.0', port=9091,
             username=os.environ['VISDOM_USERNAME'],
             password=os.environ['VISDOM_PASSWORD'])

epochs = 30
patience = 15
loaders = create_loaders(batch_size=7)
n_batches = len(loaders['train'])
warmup_steps = 7
eta_min = 1e-5
checkpoint = Checkpoint('resnet101_cosinewr_onecycle')

sched = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
    opt, T_0=n_batches, T_mult=2, eta_min=eta_min
)

global_step = sched.last_epoch

print('Number of training batches per epoch:', n_batches)

for epoch in range(1, epochs+1):
    phase = 'warm up' if epoch <= warmup_steps else 'heated'
    print(f'Epoch [{epoch}/{epochs}][{phase}]')

    if epoch == 1:
        unfreeze_layers(model, ['head'])
    elif epoch == (warmup_steps // 2):
        unfreeze_all(model)
    elif epoch == (warmup_steps + 1):
        sched = CosineDecay(
            opt, (epochs-warmup_steps)*n_batches,
            linear_frac=0.2, linear_start=eta_min)
        global_step = 0

    iteration = dict(epoch=epoch, train_loss=list(), valid_loss=list())

    for name, loader in loaders.items():
        is_training = name == 'train'
        count = 0
        metric = 0.0

        with torch.set_grad_enabled(is_training):
            for batch_no, batch in enumerate(loader):
                steps[name] += 1
                opt.zero_grad()

                y = batch['site1']['targets_one_hot'].to(device)

                out = model(
                    batch['site1']['features'].to(device),
                    batch['site2']['features'].to(device)
                )
                if is_training:
                    loss = loss_fn(out, y)
                    with amp.scale_loss(loss, opt) as scaled_loss:
                        scaled_loss.backward()
                    opt.step()
                    if (epoch == warmup_steps) and batch_no == (n_batches - 1):
                        print('Skipping last step before shifting into a new schedule')
                    else:
                        sched.step(global_step + 1)
                    global_step += 1
                    curr_lr = opt.param_groups[0]['lr']
                    vis.line(X=[steps[name]], Y=[curr_lr], win='lr', name='lr', update='append')

                avg_loss = rolling_loss[name](loss.item(), steps[name])
                iteration[f'{name}_loss'].append(avg_loss)
                y_pred = out.softmax(dim=1).argmax(dim=1)
                y_true = batch['site1']['targets'].to(device)
                acc = (y_pred == y_true).float().mean().item()
                metric += acc
                count += len(batch)

                if batch_no % log_freq == 0:
                    vis.line(X=[steps[name]], Y=[avg_loss], name=f'{name}_loss',
                             win=f'{name}_loss', update='append',
                             opts=dict(title=f'Running Loss [{name}]'))

        metric /= count
        iteration[f'{name}_acc'] = metric
        vis.line(X=[epoch], Y=[avg_loss], name=f'{name}', win='avg_loss',
                 update='append', opts=dict(title='Average Epoch Loss'))
        vis.line(X=[epoch], Y=[metric], name=f'{name}', win='accuracy',
                 update='append', opts=dict(title=f'Accuracy'))

        last_loss = iteration[f'{name}_loss'][-1]

        print(f'{name} metrics: accuracy={metric:2.3%}, loss={last_loss:.4f}')

        if is_training:
            pass

        else:
            if metric > best_metric:
                trials = 0
                best_metric = metric
                filename = checkpoint(epoch, model=model, opt=opt, amp=amp)
                print(f'Score improved! Checkpoint: {filename}')

            else:
                trials += 1
                if trials >= patience:
                    stop = True
                    break

    history.append(iteration)

    print('-' * 80)

    if stop:
        print(f'Early stopping on epoch: {epoch}')
        break

torch.save(history, f'{checkpoint.output_dir}/history.pth')
