'''
CapsNet: A PyTorch implementation of Sabour et al's paper
Dynamic Routing between Capsules (https://arxiv.org/abs/1710.09829)

Code adapted from: https://github.com/adambielski/CapsNet-pytorch
'''

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import math
from torch.autograd import Variable

verbose = False

def squash(x):
    lengths2 = x.pow(2).sum(dim=2)
    lengths = lengths2.sqrt()
    x = x * (lengths2 / (1 + lengths2) / lengths).view(x.size(0), x.size(1), 1)
    return x


class AgreementRouting(nn.Module):
    def __init__(self, input_caps, output_caps, n_iterations):
        super(AgreementRouting, self).__init__()
        self.n_iterations = n_iterations
        self.b = nn.Parameter(torch.zeros((input_caps, output_caps)))

    def forward(self, u_predict):
        batch_size, input_caps, output_caps, output_dim = u_predict.size()

        c = F.softmax(self.b)
        s = (c.unsqueeze(2) * u_predict).sum(dim=1)
        v = squash(s)

        if self.n_iterations > 0:
            b_batch = self.b.expand((batch_size, input_caps, output_caps))
            for r in range(self.n_iterations):
                v = v.unsqueeze(1)
                b_batch = b_batch + (u_predict * v).sum(-1)

                c = F.softmax(b_batch.view(-1, output_caps)).view(-1, input_caps, output_caps, 1)
                s = (c * u_predict).sum(dim=1)
                v = squash(s)

        return v


class CapsLayer(nn.Module):
    def __init__(self, input_caps, input_dim, output_caps, output_dim, routing_module):
        super(CapsLayer, self).__init__()
        self.input_dim = input_dim
        self.input_caps = input_caps
        self.output_dim = output_dim
        self.output_caps = output_caps
        self.weights = nn.Parameter(torch.Tensor(input_caps, input_dim, output_caps * output_dim))
        self.routing_module = routing_module
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.input_caps)
        self.weights.data.uniform_(-stdv, stdv)

    def forward(self, caps_output):
        caps_output = caps_output.unsqueeze(2)
        if verbose:
            print('caps_output shape')
            print(caps_output.data.shape)
            print('weights shape')
            print(self.weights.data.shape)
        u_predict = caps_output.matmul(self.weights)
        u_predict = u_predict.view(u_predict.size(0), self.input_caps, self.output_caps, self.output_dim)
        v = self.routing_module(u_predict)
        return v


class PrimaryCapsLayer(nn.Module):
    def __init__(self, input_channels, output_caps, output_dim, kernel_size, stride):
        super(PrimaryCapsLayer, self).__init__()
        self.conv = nn.Conv2d(input_channels, output_caps * output_dim, kernel_size=kernel_size, stride=stride)
        self.input_channels = input_channels
        self.output_caps = output_caps
        self.output_dim = output_dim

    def forward(self, input):
        out = self.conv(input)
        if verbose:
            print('out pc')
            print(out.data.shape)
        N, C, H, W = out.size()
        if verbose:
            print('NCHW pc')
            print(N)
            print(C)
            print(H)
            print(W)
        out = out.view(N, self.output_caps, self.output_dim, H, W)

        # will output N x OUT_CAPS x OUT_DIM
        out = out.permute(0, 1, 3, 4, 2).contiguous()
        out = out.view(out.size(0), -1, out.size(4))
        out = squash(out)
        return out


class CapsNet(nn.Module):
    def __init__(self, routing_iterations, n_classes=20):
        super(CapsNet, self).__init__()
        self.conv1 = nn.Conv2d(3, 256, kernel_size=9, stride=1)
        self.primaryCaps = PrimaryCapsLayer(256, 32, 8, kernel_size=9, stride=2)
        # outputs 6*6 for 28*28 and 56*56 for 128*128
        # 24*24 for 64*64
        self.num_primaryCaps = 32 * 24 * 24
        routing_module = AgreementRouting(self.num_primaryCaps, n_classes, routing_iterations)
        self.digitCaps = CapsLayer(self.num_primaryCaps, 8, n_classes, 16, routing_module)

    def forward(self, input):
        if verbose:
            print('Input')
            print(input.data.shape)
        x = self.conv1(input)
        x = F.relu(x)
        if verbose:
            print('After conv1 and relu')
            print(x.data.shape)
        x = self.primaryCaps(x)
        if verbose:
            print('After primary caps')
            print(x.data.shape)
        x = self.digitCaps(x)
        if verbose:
            print('After digit caps')
            print(x.data.shape)
        probs = x.pow(2).sum(dim=2).sqrt()
        return x, probs


class ReconstructionNet(nn.Module):
    def __init__(self, n_dim=16, n_classes=20):
        super(ReconstructionNet, self).__init__()
        self.fc1 = nn.Linear(n_dim * n_classes, 1000)
        self.fc2 = nn.Linear(1000, 2000)
        self.fc3 = nn.Linear(2000, 4096)
        self.n_dim = n_dim
        self.n_classes = n_classes

    def forward(self, x, target):
        #mask = Variable(torch.zeros((x.size()[0], self.n_classes)), requires_grad=False)
        #if next(self.parameters()).is_cuda:
        #    mask = mask.cuda()
        #mask.scatter_(1, target.view(-1, 1), 1.)
        #mask = mask.unsqueeze(2)
        #x = x * mask
        if verbose:
            print('Reconstruction Input')
            print(x.data.shape)
        x = x.view(-1, self.n_dim * self.n_classes)
        if verbose:
            print('Reconstruction Input after flatten')
            print(x.data.shape)
        x = F.relu(self.fc1(x))
        if verbose:
            print('Reconstruction Input after fc1')
            print(x.data.shape)
        x = F.relu(self.fc2(x))
        if verbose:
            print('Reconstruction Input after fc2')
            print(x.data.shape)
        x = self.fc3(x)
        if verbose:
            print('Reconstruction Input after fc3')
            print(x.data.shape)
        return x


class CapsNetWithReconstruction(nn.Module):
    def __init__(self, capsnet, reconstruction_net):
        super(CapsNetWithReconstruction, self).__init__()
        self.capsnet = capsnet
        self.reconstruction_net = reconstruction_net

    def forward(self, x, target):
        x, probs = self.capsnet(x)
        reconstruction = self.reconstruction_net(x, target)
        if verbose:
            print('probs shape')
            print(probs.data.shape)
            print('recons shape')
            print(reconstruction.data.shape)
        return reconstruction, probs


# class MarginLoss(nn.Module):
#     def __init__(self, m_pos, m_neg, lambda_):
#         super(MarginLoss, self).__init__()
#         self.m_pos = m_pos
#         self.m_neg = m_neg
#         self.lambda_ = lambda_
#
#     def forward(self, lengths, targets, size_average=True):
#         t = torch.zeros(lengths.size()).long()
#         if targets.is_cuda:
#             t = t.cuda()
#         t = t.scatter_(1, targets.data.view(-1, 1), 1)
#         targets = Variable(t)
#         losses = targets.float() * F.relu(self.m_pos - lengths).pow(2) + \
#                  self.lambda_ * (1. - targets.float()) * F.relu(lengths - self.m_neg).pow(2)
#         return losses.mean() if size_average else losses.sum()
