
import torch

from ..writer import DummyWriter
from ..functional import sample_gumbel_softmax, batch_linear
from ._abstract_recurrent_cell import AbstractRecurrentCell

class GumbelNACLayer(torch.nn.Module):
    """Implements the NAC (Neural Accumulator)

    Arguments:
        in_features: number of ingoing features
        out_features: number of outgoing features
    """

    def __init__(self, in_features, out_features, writer=DummyWriter(), sample_each_observation=False):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.writer = writer
        self.sample_each_observation = sample_each_observation

        # Define the temperature tau
        self.tau = torch.nn.Parameter(torch.tensor(1, dtype=torch.float32), requires_grad=False)

        # Define the target weights. Also, put 0 last such that p1 = p2 = 0
        # corresponds to p3 = 1 => w = 0.
        self.register_buffer('target_weights', torch.tensor([1, -1, 0], dtype=torch.float32))

        # Initialize a tensor, that will be the placeholder for the uniform samples
        self.register_buffer('U', torch.Tensor(out_features, in_features, 3))

        # We will only two parameters per weight, this is to prevent the redundancy
        # there would otherwise exist. This also makes it much more comparable with
        # NAC.
        self.W_hat = torch.nn.Parameter(torch.Tensor(out_features, in_features, 2))
        self.register_buffer('W_hat_k', torch.Tensor(out_features, in_features, 1))

        self.register_parameter('bias', None)

    def reset_parameters(self):
        # Initialize to zero, the source of randomness can come from the Gumbel sampling.
        torch.nn.init.constant_(self.W_hat, 0)
        torch.nn.init.constant_(self.W_hat_k, 0)
        torch.nn.init.constant_(self.tau, 1)

    def forward(self, input, reuse=False):
        # Concat trainable and non-trainable weights
        W_hat_full = torch.cat((self.W_hat, self.W_hat_k), dim=-1)  # size = [out, in, 3]

        # Convert to log-properbilities
        # NOTE: softmax(log(softmax(w)) + g) can be simplified to softmax(w + g), taking
        #           pi = softmax(W_hat_full) is just more interpretable.
        #       log_pi = W_hat_full
        log_pi = torch.nn.functional.log_softmax(W_hat_full, dim=-1)

        if self.sample_each_observation:
            # resize U, to fit the observations
            obs = input.size()[0]
            self.U.resize_(obs, self.out_features, self.in_features, 3)

            # Sample for each observation
            y = sample_gumbel_softmax(self.U, log_pi.expand(obs, -1, -1, -1), tau=self.tau, reuse=reuse)
            # select from target_weights
            W = y @ self.target_weights

            return batch_linear(input, W, self.bias)
        else:
            # Sample a quazi-1-hot encoding
            y = sample_gumbel_softmax(self.U, log_pi, tau=self.tau, reuse=reuse)
            # The final weight matrix (W), is computed by selecting from the target_weights
            W = y @ self.target_weights

            # Compute the linear multiplication as usual
            return torch.nn.functional.linear(input, W, self.bias)

    def extra_repr(self):
        return 'in_features={}, out_features={}'.format(
            self.in_features, self.out_features
        )

class GumbelNACCell(AbstractRecurrentCell):
    """Implements the Gumbel NAC (Gumbel Neural Accumulator) as a recurrent cell

    Arguments:
        input_size: number of ingoing features
        hidden_size: number of outgoing features
    """
    def __init__(self, input_size, hidden_size, **kwargs):
        super().__init__(GumbelNACLayer, input_size, hidden_size, **kwargs)
