from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ModelType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODEL_TYPE_UNSPECIFIED: _ClassVar[ModelType]
    CONTROL_POLICY: _ClassVar[ModelType]
    TRAJECTORY_POLICY: _ClassVar[ModelType]
MODEL_TYPE_UNSPECIFIED: ModelType
CONTROL_POLICY: ModelType
TRAJECTORY_POLICY: ModelType

class HealthCheckRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthCheckResponse(_message.Message):
    __slots__ = ("healthy", "detail")
    HEALTHY_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    healthy: bool
    detail: str
    def __init__(self, healthy: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class GetModelInfoRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ModelInfo(_message.Message):
    __slots__ = ("id", "name", "type", "required_sensors", "version")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_SENSORS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    type: ModelType
    required_sensors: _containers.RepeatedScalarFieldContainer[str]
    version: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., type: _Optional[_Union[ModelType, str]] = ..., required_sensors: _Optional[_Iterable[str]] = ..., version: _Optional[str] = ...) -> None: ...

class ResetEpisodeRequest(_message.Message):
    __slots__ = ("episode_id", "fixed_delta_seconds", "target_speed_mps", "spawn_index")
    EPISODE_ID_FIELD_NUMBER: _ClassVar[int]
    FIXED_DELTA_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TARGET_SPEED_MPS_FIELD_NUMBER: _ClassVar[int]
    SPAWN_INDEX_FIELD_NUMBER: _ClassVar[int]
    episode_id: str
    fixed_delta_seconds: float
    target_speed_mps: float
    spawn_index: int
    def __init__(self, episode_id: _Optional[str] = ..., fixed_delta_seconds: _Optional[float] = ..., target_speed_mps: _Optional[float] = ..., spawn_index: _Optional[int] = ...) -> None: ...

class ResetEpisodeResponse(_message.Message):
    __slots__ = ("ok", "detail")
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    detail: str
    def __init__(self, ok: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class Pose(_message.Message):
    __slots__ = ("x", "y", "z", "roll", "pitch", "yaw")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    ROLL_FIELD_NUMBER: _ClassVar[int]
    PITCH_FIELD_NUMBER: _ClassVar[int]
    YAW_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ..., roll: _Optional[float] = ..., pitch: _Optional[float] = ..., yaw: _Optional[float] = ...) -> None: ...

class Image(_message.Message):
    __slots__ = ("width", "height", "encoding", "data")
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    width: int
    height: int
    encoding: str
    data: bytes
    def __init__(self, width: _Optional[int] = ..., height: _Optional[int] = ..., encoding: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class Observation(_message.Message):
    __slots__ = ("frame_id", "timestamp", "speed_mps", "acceleration_mps2", "steering_angle", "ego_pose", "rgb_front", "route_command")
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SPEED_MPS_FIELD_NUMBER: _ClassVar[int]
    ACCELERATION_MPS2_FIELD_NUMBER: _ClassVar[int]
    STEERING_ANGLE_FIELD_NUMBER: _ClassVar[int]
    EGO_POSE_FIELD_NUMBER: _ClassVar[int]
    RGB_FRONT_FIELD_NUMBER: _ClassVar[int]
    ROUTE_COMMAND_FIELD_NUMBER: _ClassVar[int]
    frame_id: int
    timestamp: float
    speed_mps: float
    acceleration_mps2: float
    steering_angle: float
    ego_pose: Pose
    rgb_front: Image
    route_command: str
    def __init__(self, frame_id: _Optional[int] = ..., timestamp: _Optional[float] = ..., speed_mps: _Optional[float] = ..., acceleration_mps2: _Optional[float] = ..., steering_angle: _Optional[float] = ..., ego_pose: _Optional[_Union[Pose, _Mapping]] = ..., rgb_front: _Optional[_Union[Image, _Mapping]] = ..., route_command: _Optional[str] = ...) -> None: ...

class VehicleControl(_message.Message):
    __slots__ = ("throttle", "steer", "brake", "hand_brake")
    THROTTLE_FIELD_NUMBER: _ClassVar[int]
    STEER_FIELD_NUMBER: _ClassVar[int]
    BRAKE_FIELD_NUMBER: _ClassVar[int]
    HAND_BRAKE_FIELD_NUMBER: _ClassVar[int]
    throttle: float
    steer: float
    brake: float
    hand_brake: bool
    def __init__(self, throttle: _Optional[float] = ..., steer: _Optional[float] = ..., brake: _Optional[float] = ..., hand_brake: _Optional[bool] = ...) -> None: ...

class InferRequest(_message.Message):
    __slots__ = ("observation",)
    OBSERVATION_FIELD_NUMBER: _ClassVar[int]
    observation: Observation
    def __init__(self, observation: _Optional[_Union[Observation, _Mapping]] = ...) -> None: ...

class InferResponse(_message.Message):
    __slots__ = ("control",)
    CONTROL_FIELD_NUMBER: _ClassVar[int]
    control: VehicleControl
    def __init__(self, control: _Optional[_Union[VehicleControl, _Mapping]] = ...) -> None: ...
