#!/bin/env python

import os
import argparse
from inspect import getsourcefile

import requests
import torch
from clip import clip
from torch import nn
from torch.nn import functional
from torchvision import transforms
from torchvision.transforms import functional as tf
from PIL import Image
from PIL import PngImagePlugin


git_home = 'https://github.com/vladmandic/sd-extensions/blob/main/extensions/aesthetic-scorer/models'
clip_model = None
aesthetic_model = None
normalize = transforms.Normalize(mean = [0.48145466, 0.4578275, 0.40821073], std=[0.26862954, 0.26130258, 0.27577711])
device = 'cuda' if torch.cuda.is_available() else 'cpu'


class AestheticMeanPredictionLinearModel(nn.Module):
    def __init__(self, feats_in):
        super().__init__()
        self.linear = nn.Linear(feats_in, 1)

    def forward(self, tensor):
        x = functional.normalize(tensor, dim=-1) * tensor.shape[-1] ** 0.5
        return self.linear(x)


def torch_gc():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def find_model(params):
    model_path = os.path.join(os.path.dirname(getsourcefile(lambda:0)), 'models', params.model)
    if not os.path.exists(model_path):
        try:
            print(f'Aesthetic scorer downloading model: {model_path}')
            url = f"{git_home}/{params.model}?raw=true"
            r = requests.get(url, timeout=60)
            with open(model_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            print(f'Aesthetic scorer downloading model failed: {model_path}:', e)
    return model_path


def load_models(params):
    global clip_model
    global aesthetic_model
    if clip_model is None:
        print(f'Loading CLiP model: {params.clip} ')
        clip_model, _clip_preprocess = clip.load(params.clip, jit = False, device = device)
        clip_model.eval().requires_grad_(False)
        idx = torch.tensor(0).to(device)
        first_embedding = clip_model.token_embedding(idx)
        expected_shape = first_embedding.shape[0]
        aesthetic_model = AestheticMeanPredictionLinearModel(expected_shape)
        print(f'Loading Aesthetic Score model: {params.model} ')
        model_path = find_model(params)
        aesthetic_model.load_state_dict(torch.load(model_path))
        clip_model = clip_model.to(device)
        aesthetic_model = aesthetic_model.to(device)
    return


def aesthetic_score(fn, params):
    global clip_model
    global aesthetic_model
    try:
        img = Image.open(fn)
    except Exception as e:
        print('Aesthetic scorer failed to open image:', e)
        return 0   
    load_models(params)
    img = img.convert('RGB')
    img = tf.resize(img, 224, transforms.InterpolationMode.LANCZOS) # resizes smaller edge
    img = tf.center_crop(img, (224,224)) # center crop non-squared images
    img = tf.to_tensor(img).to(device)
    img = normalize(img)
    encoded = clip_model.encode_image(img[None, ...]).float()
    clip_image_embed = functional.normalize(encoded, dim = -1)
    score = aesthetic_model(clip_image_embed)
    score = round(score.item(), 2)
    print(f'Aesthetic score: {score} for image {fn}')    
    if params.save: save_score(fn, score) 
    return score


def save_score(fn, score):
    try:
        img = Image.open(fn)
    except Exception as e:
        print('Aesthetic scorer failed to open image:', e)
        return 0   
    if "Score: " in img.info['parameters']:
        print(f'Not writing score to image, already present: {img.info["parameters"].split("Score: ")[1][:4]}')
    else:
        print('Writing score to image...')
        img.info['parameters'] += f', Score: {score}'
        metadata = PngImagePlugin.PngInfo()
        for key, value in img.info.items():
            if isinstance(key, str) and isinstance(value, str):
                metadata.add_text(key, value)        
        try:
            img.save(fn, pnginfo=metadata)
            print('Done!')
        except Exception as e:
            print('Aesthetic scorer failed to write score to image:', e)
            return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'generate model previews')
    parser.add_argument('--model', type = str, default = 'sac_public_2022_06_29_vit_l_14_linear.pth', required = False, help = 'CLiP model')
    parser.add_argument('--clip', type = str, default = 'ViT-L/14', required = False, help = 'CLiP model')
    parser.add_argument('input', type = str, nargs = '*', help = 'input image(s) or folder(s)')
    parser.add_argument('--save', type = bool, default = False, required = False, help = 'Save score to image file. WARNING! This will discard existing Exif tags!' )
    params = parser.parse_args()
    for fn in params.input:
        if os.path.isfile(fn):
            aesthetic_score(fn, params)
        elif os.path.isdir(fn):
            for root, dirs, files in os.walk(fn):
                for file in files:
                    aesthetic_score(os.path.join(root, file), params)
    torch_gc()
