"""gRPC model gateway."""

#: gRPC defaults to a 4 MB frame, which a multi-camera rig blows straight
#: through: three 1600x900 views are 13 MB raw. Both ends have to agree on the
#: limit, so it lives here rather than in either one of them.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MESSAGE_SIZE_OPTIONS = [
    ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
]
