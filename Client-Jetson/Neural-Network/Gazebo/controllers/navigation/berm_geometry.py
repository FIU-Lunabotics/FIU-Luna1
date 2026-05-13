import math
from dataclasses import dataclass


CORNER_ALIASES = {
    "sw": "southwest",
    "south_west": "southwest",
    "southwest": "southwest",
    "se": "southeast",
    "south_east": "southeast",
    "southeast": "southeast",
    "nw": "northwest",
    "north_west": "northwest",
    "northwest": "northwest",
    "ne": "northeast",
    "north_east": "northeast",
    "northeast": "northeast",
}

HEADING_YAW_RAD = {
    "east": 0.0,
    "north": math.pi / 2.0,
    "west": math.pi,
    "south": -math.pi / 2.0,
}


@dataclass(frozen=True)
class BermGeometryConfig:
    arena_width_m: float
    arena_height_m: float
    start_corner: str
    start_heading: str
    berm_center_forward_m: float
    berm_center_left_m: float
    berm_width_m: float
    berm_length_m: float
    berm_yaw_deg: float = 0.0


@dataclass(frozen=True)
class BermRegion:
    center_x: float
    center_y: float
    yaw_rad: float
    width_m: float
    length_m: float
    corners: tuple
    start_x: float
    start_y: float
    start_yaw_rad: float
    inside_arena: bool


def normalize_corner(corner):
    key = str(corner).strip().lower().replace("-", "_").replace(" ", "_")
    if key not in CORNER_ALIASES:
        raise ValueError(
            "start_corner must be one of southwest, southeast, northwest, northeast"
        )
    return CORNER_ALIASES[key]


def normalize_heading(heading):
    key = str(heading).strip().lower()
    if key not in HEADING_YAW_RAD:
        raise ValueError("start_heading must be one of north, east, south, west")
    return key


def corner_position(corner, arena_width_m, arena_height_m):
    corner = normalize_corner(corner)
    if corner == "southwest":
        return 0.0, 0.0
    if corner == "southeast":
        return arena_width_m, 0.0
    if corner == "northwest":
        return 0.0, arena_height_m
    return arena_width_m, arena_height_m


def heading_vectors(heading):
    yaw = HEADING_YAW_RAD[normalize_heading(heading)]
    forward = (math.cos(yaw), math.sin(yaw))
    left = (-math.sin(yaw), math.cos(yaw))
    return yaw, forward, left


def rectangle_corners(center_x, center_y, width_m, length_m, yaw_rad):
    half_width = width_m / 2.0
    half_length = length_m / 2.0
    local_corners = (
        (-half_width, -half_length),
        (half_width, -half_length),
        (half_width, half_length),
        (-half_width, half_length),
    )

    cos_yaw = math.cos(yaw_rad)
    sin_yaw = math.sin(yaw_rad)
    corners = []
    for local_x, local_y in local_corners:
        world_x = center_x + local_x * cos_yaw - local_y * sin_yaw
        world_y = center_y + local_x * sin_yaw + local_y * cos_yaw
        corners.append((world_x, world_y))
    return tuple(corners)


def region_inside_arena(corners, arena_width_m, arena_height_m):
    return all(
        0.0 <= x <= arena_width_m and 0.0 <= y <= arena_height_m for x, y in corners
    )


def validate_config(config):
    if config.arena_width_m <= 0.0:
        raise ValueError("arena_width_m must be greater than zero")
    if config.arena_height_m <= 0.0:
        raise ValueError("arena_height_m must be greater than zero")
    if config.berm_width_m <= 0.0:
        raise ValueError("berm_width_m must be greater than zero")
    if config.berm_length_m <= 0.0:
        raise ValueError("berm_length_m must be greater than zero")
    normalize_corner(config.start_corner)
    normalize_heading(config.start_heading)


def compute_berm_region(config):
    validate_config(config)

    start_x, start_y = corner_position(
        config.start_corner,
        config.arena_width_m,
        config.arena_height_m,
    )
    start_yaw_rad, forward, left = heading_vectors(config.start_heading)

    center_x = (
        start_x
        + config.berm_center_forward_m * forward[0]
        + config.berm_center_left_m * left[0]
    )
    center_y = (
        start_y
        + config.berm_center_forward_m * forward[1]
        + config.berm_center_left_m * left[1]
    )
    berm_yaw_rad = math.radians(config.berm_yaw_deg)
    corners = rectangle_corners(
        center_x,
        center_y,
        config.berm_width_m,
        config.berm_length_m,
        berm_yaw_rad,
    )

    return BermRegion(
        center_x=center_x,
        center_y=center_y,
        yaw_rad=berm_yaw_rad,
        width_m=config.berm_width_m,
        length_m=config.berm_length_m,
        corners=corners,
        start_x=start_x,
        start_y=start_y,
        start_yaw_rad=start_yaw_rad,
        inside_arena=region_inside_arena(
            corners,
            config.arena_width_m,
            config.arena_height_m,
        ),
    )
