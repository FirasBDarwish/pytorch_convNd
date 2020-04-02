import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Callable
import math

class ConvNd(nn.Module):
    """Some Information about ConvNd"""
    def __init__(self,in_channels: int,
                 out_channels: int,
                 num_dims: int,
                 kernel_size: Tuple,
                 stride,
                 padding,
                 padding_mode = 'zeros',
                 dilation: int = 1,
                 groups: int = 1,
                 rank: int = 0,
                 use_bias: bool = True,
                 bias_initializer: Callable = None,
                 kernel_initializer: Callable = None):
        super(ConvNd, self).__init__()

        # ---------------------------------------------------------------------
        # Assertions for constructor arguments
        # ---------------------------------------------------------------------
        if not isinstance(kernel_size, Tuple):
            kernel_size = tuple(kernel_size for _ in range(num_dims))
        if not isinstance(stride, Tuple):
            stride = tuple(stride for _ in range(num_dims))
        if not isinstance(padding, Tuple):
            padding = tuple(padding for _ in range(num_dims))

        # This parameter defines which Pytorch convolution to use as a base, for 3 Conv2D is used
        # for conv4D max_dims
        max_dims = num_dims-1
        self.conv_f = (nn.Conv1d, nn.Conv2d, nn.Conv3d)[max_dims - 1]
        
        assert len(kernel_size) == num_dims, \
            'nD kernel size expected!'
        assert len(stride) == num_dims, \
            'nD stride size expected!'
        assert len(padding) == num_dims, \
            'nD padding size expected!'
        assert dilation == 1, \
            'Dilation rate other than 1 not yet implemented!'
        # assert groups == 1, \
            # 'Groups other than 1 not yet implemented!'
        # assert num_dims >=max_dims, \
        #     'This function works for more than 3 dimensions, for less use torch implementation'

        # ---------------------------------------------------------------------
        # Store constructor arguments
        # ---------------------------------------------------------------------
        self.rank = rank
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.num_dims = num_dims
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.padding_mode = padding_mode
        self.groups = groups
        self.use_bias = use_bias
        if use_bias:
            self.bias = nn.Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter('bias', None)
        self.bias_initializer = bias_initializer
        self.kernel_initializer = kernel_initializer

        # ---------------------------------------------------------------------
        # Construct 3D convolutional layers
        # ---------------------------------------------------------------------
        if self.bias_initializer is not None:
            if self.use_bias:
                self.bias_initializer(self.bias)
        # Use a ModuleList to store layers to make the Conv4d layer trainable
        self.conv_layers = torch.nn.ModuleList()

        # Compute the next dimension, so for a conv4D, get index 3
        next_dim_len = self.kernel_size[0] 
        
        for _ in range(next_dim_len):
            if self.num_dims-1 != max_dims:
                # Initialize a Conv_n-1_D layer
                conv_layer = ConvNd(in_channels=self.in_channels,
                                            out_channels=self.out_channels,
                                            use_bias=self.use_bias,
                                            num_dims=self.num_dims-1,
                                            kernel_size=self.kernel_size[1:],
                                            stride=self.stride[1:],
                                            rank=self.rank-1,
                                            groups=self.groups,
                                            padding=self.padding[1:],
                                            padding_mode=self.padding_mode)

            else:
                # Initialize a Conv layer
                # bias should only be applied by the top most layer, so we disable bias in the internal convs
                conv_layer = self.conv_f(in_channels=self.in_channels,
                                            out_channels=self.out_channels,
                                            bias=False,
                                            kernel_size=self.kernel_size[1:],
                                            stride=self.stride[1:],
                                            padding=self.padding[1:], 
                                            padding_mode=self.padding_mode,
                                            groups=self.groups,)

            # Apply initializer functions to weight and bias tensor
            if self.kernel_initializer is not None:
                self.kernel_initializer(conv_layer.weight)

            # Store the layer
            self.conv_layers.append(conv_layer)

    # -------------------------------------------------------------------------

    def forward(self, input):
        padding = list(self.padding)
        # Pad input if this is the parent convolution ie rank=0
        if self.rank==0:
            inputShape = list(input.shape)
            inputShape[2] += 2*self.padding[0]
            padSize = (0,0,self.padding[0],self.padding[0])
            padding[0] = 0
            if self.padding_mode is 'zeros':
                input = F.pad(input.view(input.shape[0],input.shape[1],input.shape[2],-1),padSize,'constant',0).view(inputShape)
            else:
                input = F.pad(input.view(input.shape[0],input.shape[1],input.shape[2],-1),padSize,'reflect').view(inputShape)
        # Define shortcut names for dimensions of input and kernel
        (b, c_i) = tuple(input.shape[0:2])
        size_i = tuple(input.shape[2:])
        size_k = self.kernel_size

        # Compute the size of the output tensor based on the zero padding
        size_o = tuple([math.floor((size_i[x] + 2 * padding[x] - size_k[x]) / self.stride[x] + 1) for x in range(len(size_i))])
        # (math.floor((l_i + 2 * self.padding - l_k) / self.stride + 1),

        # Compute size of the output without stride
        size_ons = tuple([size_i[x] - size_k[x] + 1 for x in range(len(size_i))])


        # Output tensors for each 3D frame
        frame_results = size_o[0] * [torch.zeros((b,self.out_channels) + size_o[1:], device=input.device)]
        empty_frames = size_o[0] * [None]

        # Convolve each kernel frame i with each input frame j
        for i in range(size_k[0]):

            for j in range(size_i[0]):

                # Add results to this output frame
                out_frame = j - (i - size_k[0] // 2) - (size_i[0] - size_ons[0]) // 2 - (1-size_k[0]%2) 
                k_center_position = out_frame % self.stride[0]

                out_frame = math.floor(out_frame / self.stride[0])
                if out_frame < 0 or out_frame >= size_o[0] or k_center_position != 0:
                    continue

                # Prepate input for next dimmension
                conv_input = input.view(b, c_i, size_i[0], -1)
                conv_input = conv_input[:, :, j, :].view((b, c_i) + size_i[1:])

                # Convolve
                frame_conv = \
                    self.conv_layers[i](conv_input)

                # Store in computed output position at current dimension
                if empty_frames[out_frame] is None:
                    frame_results[out_frame] = frame_conv
                    empty_frames[out_frame] = 1
                else:
                    frame_results[out_frame] += frame_conv

        result = torch.stack(frame_results, dim=2)
        if self.use_bias:
            resultShape = result.shape
            result = result.view(b,resultShape[1],-1)
            for k in range(self.out_channels):
                result[:,k,:] += self.bias[k]
            return result.view(resultShape)
        else:
            return result


class ConvTransposeNd(nn.Module):
    """Some Information about ConvNd"""
    def __init__(self,in_channels: int,
                 out_channels: int,
                 num_dims: int,
                 kernel_size: Tuple,
                 stride,
                 padding,
                 padding_mode = 'zeros',
                 dilation: int = 1,
                 groups: int = 1,
                 rank: int = 0,
                 use_bias: bool = True,
                 bias_initializer: Callable = None,
                 kernel_initializer: Callable = None):
        super(ConvTransposeNd, self).__init__()

        # ---------------------------------------------------------------------
        # Assertions for constructor arguments
        # ---------------------------------------------------------------------
        if not isinstance(kernel_size, Tuple):
            kernel_size = tuple(kernel_size for _ in range(num_dims))
        if not isinstance(stride, Tuple):
            stride = tuple(stride for _ in range(num_dims))
        if not isinstance(padding, Tuple):
            padding = tuple(padding for _ in range(num_dims))

        # This parameter defines which Pytorch convolution to use as a base, for 3 Conv2D is used
        max_dims = num_dims-1
        self.conv_f = (nn.ConvTranspose1d, nn.ConvTranspose2d, nn.ConvTranspose3d)[max_dims - 1]
        assert len(kernel_size) == num_dims, \
            '4D kernel size expected!'
        assert len(stride) == num_dims, \
            '4D stride size expected!'
        assert len(padding) == num_dims, \
            '4D padding size expected!'
        assert dilation == 1, \
            'Dilation rate other than 1 not yet implemented!'
        # assert groups == 1, \
            # 'Groups other than 1 not yet implemented!'
        # assert num_dims >=max_dims, \
        #     'This function works for more than 3 dimensions, for less use torch implementation'

# ---------------------------------------------------------------------
        # Store constructor arguments
        # ---------------------------------------------------------------------
        self.rank = rank
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.num_dims = num_dims
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.padding_mode = padding_mode
        self.groups = groups
        self.use_bias = use_bias
        if use_bias:
            self.bias = nn.Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter('bias', None)
        self.bias_initializer = bias_initializer
        self.kernel_initializer = kernel_initializer

        # ---------------------------------------------------------------------
        # Construct 3D convolutional layers
        # ---------------------------------------------------------------------
        if self.bias_initializer is not None:
            if self.use_bias:
                self.bias_initializer(self.bias)
        # Use a ModuleList to store layers to make the Conv4d layer trainable
        self.conv_layers = torch.nn.ModuleList()

        # Compute the next dimension, so for a conv4D, get index 3
        next_dim_len = self.kernel_size[0] 
        
        for _ in range(next_dim_len):
            if self.num_dims-1 != max_dims:
                # Initialize a Conv_n-1_D layer
                conv_layer = ConvTransposeNd(in_channels=self.in_channels,
                                            out_channels=self.out_channels,
                                            use_bias=self.use_bias,
                                            num_dims=self.num_dims-1,
                                            kernel_size=self.kernel_size[1:],
                                            stride=self.stride[1:],
                                            rank=self.rank-1,
                                            groups=self.groups,
                                            padding=self.padding[1:],
                                            padding_mode=self.padding_mode)

            else:
                # Initialize a Conv layer
                # bias should only be applied by the top most layer, so we disable bias in the internal convs
                conv_layer = self.conv_f(in_channels=self.in_channels,
                                            out_channels=self.out_channels,
                                            bias=False,
                                            kernel_size=self.kernel_size[1:],
                                            stride=self.stride[1:],
                                            padding=self.padding[1:],
                                            padding_mode=self.padding_mode,
                                            groups=self.groups,)

            # Apply initializer functions to weight and bias tensor
            if self.kernel_initializer is not None:
                self.kernel_initializer(conv_layer.weight)

            # Store the layer
            self.conv_layers.append(conv_layer)

    # -------------------------------------------------------------------------

    def forward(self, input):
        # padding = list(self.padding)
        # Pad input if this is the parent convolution ie rank=0
        # if self.rank==0:
        #     inputShape = list(input.shape)
        #     inputShape[2] += 2*self.padding[0]
        #     padSize = (0,0,self.padding[0],self.padding[0])
        #     padding[0] = 0
        #     if self.padding_mode is 'zeros':
        #         input = F.pad(input.view(input.shape[0],input.shape[1],input.shape[2],-1),padSize,'constant',0).view(inputShape)
        #     else:
        #         input = F.pad(input.view(input.shape[0],input.shape[1],input.shape[2],-1),padSize,'reflect').view(inputShape)
        # Define shortcut names for dimensions of input and kernel
        (b, c_i) = tuple(input.shape[0:2])
        size_i = tuple(input.shape[2:])
        size_k = self.kernel_size

        # Compute the size of the output tensor based on the zero padding
        size_o = tuple([(size_i[x] - 1) * self.stride[x] - 2 * self.padding[x] + (size_k[x]-1) + 1 for x in range(len(size_i))])
        
        # Output tensors for each 3D frame
        frame_results = size_o[0] * [torch.zeros((b,self.out_channels) + size_o[1:]).cuda()] # todo: add .cuda()
        empty_frames = size_o[0] * [None]

        # Convolve each kernel frame i with each input frame j
        for i in range(size_k[0]):

            for j in range(size_i[0]):

                # Add results to this output frame
                out_frame = (i+size_k[0]//2) + j - size_k[0]//2 - self.padding[0]

                if out_frame < 0 or out_frame >= size_o[0]:
                    continue

                # Prepate input for next dimmension
                conv_input = input.view(b, c_i, size_i[0], -1)
                conv_input = conv_input[:, :, j, :].view((b, c_i) + size_i[1:])

                # Convolve
                frame_conv = \
                    self.conv_layers[i](conv_input)

                if empty_frames[out_frame] is None:
                    frame_results[out_frame] = frame_conv
                    empty_frames[out_frame] = 1
                else:
                    frame_results[out_frame] += frame_conv

        result = torch.stack(frame_results, dim=2)

        if self.use_bias:
            resultShape = result.shape
            result = result.view(b,resultShape[1],-1)
            for k in range(self.out_channels):
                result[:,k,:] += self.bias[k]
            return result.view(resultShape)
        else:
            return result




class DenseBlock(nn.Module):
    def __init__(self, nChans, ks):
        super(DenseBlock, self).__init__()
        padding = math.floor(ks/2)
        self.conv1 = nn.Sequential(
            nn.Conv2d(nChans, nChans, ks, padding=padding),
            nn.ReLU())
        self.conv2 = nn.Sequential(
            nn.Conv2d(nChans, nChans, ks, padding=padding),
            nn.ReLU())
        self.conv3 = nn.Sequential(
            nn.Conv2d(nChans, nChans, ks, padding=padding),
            nn.ReLU())
        self.convOut = nn.Conv2d(nChans,nChans, kernel_size=1)

    def forward(self, x):
        x1 = self.conv1(x)
        x2 = self.conv2(x+x1)
        x3 = self.conv3(x+x1+x2)
        x4 = self.convOut(x+x1+x2+x3)
        return x+x4
