import os
import torch
import torchvision
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter
import torch.nn as nn
import torch.optim as optim
from torchsummary import summary
from torchvision import datasets, models, transforms
import torchvision.transforms.functional as f
from tqdm import tqdm
import numpy as np
import matplotlib.pyplot as plt
import time
import copy
from PIL import Image
import random


class BurnDataset(Dataset):

    """
    Class to create our customized dataset
    """

    def __init__(self, inputs_dir, masks_dir, train=True):
        self.inputs_dir = inputs_dir
        self.masks_dir = masks_dir
        self.data = os.listdir(self.inputs_dir)
        self.train = train

    def __len__(self):
        return len(self.data)

    def preprocess(self, img):
        img_array = np.array(img)
        img_array = img_array.transpose((2, 0, 1))
        if img_array.max() > 1:
            img_array = img_array / 255
        return img_array

    # def transform(self, img, mask):
    #     if self.train:
    #         if random.random() > 0.5:
    #             img = f.hflip(img)
    #             mask = f.hflip(mask)
    #         if random.random() > 0.5:
    #             img = f.vflip(img)
    #             mask = f.vflip(mask)
    #     return img, mask

    def __getitem__(self, index):
        file_name = self.data[index].split(".")[0]
        input_file = os.path.join(self.inputs_dir, file_name + ".png")
        mask_file = os.path.join(self.masks_dir, file_name + ".png")
        image = Image.open(input_file)
        mask = Image.open(mask_file)
        # timage, tmask = self.transform(image, mask)
        image = self.preprocess(image)
        mask = np.array(mask) / 255
        im, ground_t = torch.from_numpy(image).type(torch.FloatTensor), torch.from_numpy(mask).type(torch.FloatTensor)
        return im, ground_t


# U-net Blocks


class DownConv(nn.Module):

    """
    One Max Pooling
    Two Convolution -> Batch Normalization -> ReLu
    """

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.downblock = nn.Sequential(
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(self.in_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(self.out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(self.out_channels),
            nn.ReLU(inplace=True)
        )

    def forward(self, x):
        return self.downblock(x)


class UpConv(nn.Module):

    """"
    One up convolution
    Two Convolution -> Batch Normalization -> ReLu
    """

    def __init__(self, in_channels, out_channels, bilinear=True):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.mid_channels = self.in_channels // 2
        self.bilinear = bilinear
        if self.bilinear:
            self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
            self.conv = nn.Sequential(
                nn.Conv2d(self.in_channels, self.mid_channels, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(self.mid_channels),
                nn.ReLU(inplace=True),
                nn.Conv2d(self.mid_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(self.out_channels),
                nn.ReLU(inplace=True)
            )
        else:
            self.up = nn.ConvTranspose2d(self.in_channels, self.in_channels // 2, kernel_size=2, stride=2)
            self.conv = nn.Sequential(
                nn.Conv2d(self.in_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(self.out_channels),
                nn.ReLU(inplace=True),
                nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(self.out_channels),
                nn.ReLU(inplace=True)
            )

    def forward(self, x1, x2):
        x1 = self.up(x1)
        dif_h = x2.size()[2] - x1.size()[2]
        dif_w = x2.size()[3] - x1.size()[3]
        x1 = f.pad(x1, [dif_w // 2, dif_w - dif_w // 2, dif_h // 2, dif_h - dif_h // 2])
        x = torch.cat([x2, x1], dim=1)
        x = self.conv(x)
        return x


class DoubleConv(nn.Module):

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.doubleconv = nn.Sequential(
            nn.Conv2d(self.in_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(self.out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(self.out_channels),
            nn.ReLU(inplace=True)
        )

    def forward(self, x):
        return self.doubleconv(x)


class OutConv(nn.Module):

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x):
        return self.conv(x)

# Complete model


class UNet(nn.Module):
    def __init__(self, n_channels, n_classes, bilinear=True):
        super().__init__()
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.bilinear = bilinear

        factor = 2 if bilinear else 1

        self.l1 = DoubleConv(self.n_channels, 64)
        self.down1 = DownConv(64, 128)
        self.down2 = DownConv(128, 256)
        self.down3 = DownConv(256, 512)
        self.down4 = DownConv(512, 1024 // factor)
        self.up1 = UpConv(1024, 512 // factor, bilinear)
        self.up2 = UpConv(512, 256 // factor, bilinear)
        self.up3 = UpConv(256, 128 // factor, bilinear)
        self.up4 = UpConv(128, 64, bilinear)
        self.out = OutConv(64, self.n_classes)

    def forward(self, x):
        x1 = self.l1(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        logits = self.out(x)
        return logits


def train_model(model, device, epochs, batch_size, lr, n_train, train_dataloader, val_dataloader):

    writer = SummaryWriter(comment=f'LR_{lr}_BS_{batch_size}')
    global_step = 0
    n_val = len(val_dataloader)

    optimizer = optim.RMSprop(model.parameters(), lr=lr, weight_decay=1e-8, momentum=0.9)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'min', patience=2)
    criterion = nn.CrossEntropyLoss()
    model = model.to(device)

    for epoch in range(epochs):

        model.train()

        epoch_loss = 0

        with tqdm(total=n_train, desc=f'Epoch {epoch + 1}/{epochs}', unit='img') as pbar:
            for images, masks in train_dataloader:
                images = images.to(device, dtype=torch.float32)
                masks = masks.to(device, dtype=torch.long)
                predictions = model(images)
                loss = criterion(predictions, masks)
                epoch_loss += loss.item()

                pbar.set_postfix(**{'Loss (batch)': loss.item()})

                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_value_(model.parameters(), 0.1)
                optimizer.step()

                pbar.update(images.shape[0])
                global_step += 1

                if global_step % (n_train // (10 * batch_size)) == 0:
                    model.eval()
                    val_run_loss = 0
                    with tqdm(total=n_val, desc='Validation round', unit='batch', leave=False) as val_pbar:
                        for val_images, val_masks in val_dataloader:
                            val_images = val_images.to(device, dtype=torch.float32)
                            val_masks = val_masks.to(device, dtype=torch.long)

                            with torch.no_grad():
                                val_predictions = model(val_images)

                            val_run_loss += criterion(val_predictions, val_masks).item()
                            val_pbar.update()

                    model.train()
                    val_loss = val_run_loss / n_val
                    scheduler.step(val_loss)
                    writer.add_scalar('learning_rate', optimizer.param_groups[0]['lr'], global_step)
                    writer.add_scalar('Loss/validation', val_loss, global_step)
                    writer.add_images('images', images, global_step)

    writer.close()
    return model


if __name__ == "__main__":

    # Paths
    data_dir = r"F:\Users\user\Desktop\PURDUE\Research_Thesis\Thesis_Data\RGB\Dataset"
    labels_dir = r"F:\Users\user\Desktop\PURDUE\Research_Thesis\Thesis_Data\RGB\Masks_Greyscale"

    # Model inputs
    batch_size = 4
    device = torch.device("cuda:0")
    learning_rate = 0.001
    n_epochs = 2
    n_classes = 3
    n_channels = 3

    # Create training and validation datasets
    training_dataset = BurnDataset(os.path.join(data_dir, "Train"), os.path.join(labels_dir, "Train"))
    val_dataset = BurnDataset(os.path.join(data_dir, "Val"), os.path.join(labels_dir, "Val"))

    # Create training and validation dataloaders
    training_dataloader = DataLoader(training_dataset, batch_size=batch_size, shuffle=True, num_workers=8, pin_memory=True)
    val_dataloader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=8, pin_memory=True, drop_last=True)

    # Initialize model
    model = UNet(n_channels, n_classes)

    # Training and validation
    segmentation_model = train_model(model, device, n_epochs, batch_size, learning_rate, len(training_dataset),
                                     training_dataloader, val_dataloader)

