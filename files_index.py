# -----------------------------------------
# THIS FILE WAS AUTOGENERATED! DO NOT EDIT!
# -----------------------------------------
# file to edit: 00b_files_index.ipynb

import glob
import os
import pickle
from pudb import set_trace
import sys

import pandas as pd
from tqdm import tqdm as tqdm

try:
    extended
except NameError:
    sys.path.insert(0, 'rxrx1-utils')
    import rxrx.io as rio

from basedir import ROOT, TRAIN, TEST, SAMPLE


def collect_records(basedir):
    """Globs the folder with images and constructs data frame with image paths
    and additional meta-information.
    """
    print(f'Collecting records from folder: {basedir}.')
    records = []
    columns = ['experiment', 'plate', 'well', 'site', 'channel', 'filename']
    for path in glob.glob(f'{basedir}/**/*.png', recursive=True):
        exp, plate, filename = os.path.relpath(path, start=basedir).split('/')
        basename, _ = os.path.splitext(filename)
        well, site, channel = basename.split('_')
        records.append([exp, int(plate[-1]), well, int(site[1:]), int(channel[1:]), path])
    records = pd.DataFrame(records, columns=columns)
    records['id_code'] = records[['experiment', 'plate', 'well']].apply(
        lambda r: '_'.join(map(str, r)), axis='columns')
    return records.drop(columns=['experiment', 'plate', 'well'])


def build_files_index():
    print('Reading training data meta.')
    trn_df = collect_records(TRAIN)
    trn_df['dataset'] = 'train'
    print('Reading testing data meta.')
    tst_df = collect_records(TEST)
    tst_df['dataset'] = 'test'
    print('Merging meta-information into single index table.')
    df = pd.concat([trn_df, tst_df], axis='rows')
    keys = ['id_code', 'site', 'dataset']
    df.set_index(keys, inplace=True)
    meta = rio.combine_metadata(base_path=ROOT)
    meta = meta.reset_index().set_index(keys)
    return df.join(meta).reset_index()


def generate_samples(files_df):
    print('Generating samples.')
    samples = []
    for _, g in tqdm(files_df.groupby(['id_code', 'site', 'dataset'])):
        g = g.sort_values(by='channel')
        [sample] = g.drop_duplicates('id_code').to_dict('records')
        images = list(zip(g.channel, g.filename))
        sirna = sample['sirna']
        sample['images'] = images
        sample['sirna'] = 0 if pd.isna(sirna) else int(sirna)
        sample.pop('filename')
        samples.append(sample)
    return samples


dataset = generate_samples(build_files_index())


with open(ROOT/'tmp'/'meta.pickle', 'wb') as f:
    pickle.dump(obj=dataset, file=f)
