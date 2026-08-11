"""End-to-end driving network.

    front camera + speed  ->  control  +  short trajectory

A ResNet-18 image encoder and a small speed encoder are fused and read by two
heads. This is the CILRS / TCP shape: predicting both what to do now and where
to be over the next two seconds.

The trajectory head is not decoration. Training a control regressor alone lets
it settle on the average action - mostly "hold this throttle" - because that is
what minimises the loss on a highway. Asking the same features to also say
where the road goes forces them to encode geometry, and it produces an output a
trajectory controller can execute, which is the interface every waypoint-based
driving model in the CARLA literature exposes.
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torchvision.models import ResNet18_Weights, resnet18


class DrivingNet(nn.Module):
    def __init__(self, num_waypoints: int = 4, pretrained: bool = True) -> None:
        super().__init__()
        self.num_waypoints = num_waypoints

        weights = ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
        backbone = resnet18(weights=weights)
        # Everything except the classifier: (B, 512, H/32, W/32) -> pooled 512.
        self.encoder = nn.Sequential(*list(backbone.children())[:-1])

        self.speed_encoder = nn.Sequential(
            nn.Linear(1, 64), nn.ReLU(inplace=True),
            nn.Linear(64, 64), nn.ReLU(inplace=True),
        )

        self.trunk = nn.Sequential(
            nn.Linear(512 + 64, 256), nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(256, 128), nn.ReLU(inplace=True),
        )

        # steer in [-1, 1] via tanh; throttle and brake in [0, 1] via sigmoid.
        # Bounding the outputs in the network means the simulator's clamp never
        # has to salvage anything, and the loss cannot be reduced by predicting
        # impossible actions.
        self.steer_head = nn.Sequential(nn.Linear(128, 64), nn.ReLU(inplace=True),
                                        nn.Linear(64, 1), nn.Tanh())
        self.pedal_head = nn.Sequential(nn.Linear(128, 64), nn.ReLU(inplace=True),
                                        nn.Linear(64, 2), nn.Sigmoid())
        self.waypoint_head = nn.Sequential(nn.Linear(128, 128), nn.ReLU(inplace=True),
                                           nn.Linear(128, num_waypoints * 2))

    def forward(self, image: torch.Tensor, speed: torch.Tensor
                ) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.encoder(image).flatten(1)
        fused = self.trunk(torch.cat([features, self.speed_encoder(speed)], dim=1))
        control = torch.cat([self.steer_head(fused), self.pedal_head(fused)], dim=1)
        return control, self.waypoint_head(fused)


#: ImageNet statistics, since the backbone is pretrained on it.
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


def normalise(images: torch.Tensor) -> torch.Tensor:
    return (images - MEAN.to(images.device)) / STD.to(images.device)
