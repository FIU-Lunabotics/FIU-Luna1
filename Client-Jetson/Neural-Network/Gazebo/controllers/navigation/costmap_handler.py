import math


class CostmapBuilder:
    def __init__(
        self,
        *,
        resolution,
        width_m,
        height_m,
        origin_x,
        origin_y,
        min_obstacle_z,
        max_obstacle_z,
        inflation_radius_m,
        occupied_cost=100,
        free_cost=0,
    ):
        self.resolution = float(resolution)
        self.width_m = float(width_m)
        self.height_m = float(height_m)
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self.min_obstacle_z = float(min_obstacle_z)
        self.max_obstacle_z = float(max_obstacle_z)
        self.inflation_radius_m = float(inflation_radius_m)
        self.occupied_cost = int(occupied_cost)
        self.free_cost = int(free_cost)

        self.width_cells = max(1, int(math.ceil(self.width_m / self.resolution)))
        self.height_cells = max(1, int(math.ceil(self.height_m / self.resolution)))
        self.inflation_radius_cells = max(
            0, int(math.ceil(self.inflation_radius_m / self.resolution))
        )

    def build_grid(self, points):
        occupied_cells = set()

        for x, y, z in points:
            if z < self.min_obstacle_z or z > self.max_obstacle_z:
                continue

            cell = self.world_to_cell(x, y)
            if cell is not None:
                occupied_cells.add(cell)

        if self.inflation_radius_cells > 0 and occupied_cells:
            occupied_cells = self.inflate_cells(occupied_cells)

        return self.cells_to_grid(occupied_cells)

    def cells_to_grid(self, occupied_cells):
        grid = [self.free_cost] * (self.width_cells * self.height_cells)
        for cell_x, cell_y in occupied_cells:
            index = cell_y * self.width_cells + cell_x
            grid[index] = self.occupied_cost
        return grid

    def world_to_cell(self, x, y):
        cell_x = int(math.floor((x - self.origin_x) / self.resolution))
        cell_y = int(math.floor((y - self.origin_y) / self.resolution))

        if cell_x < 0 or cell_y < 0:
            return None
        if cell_x >= self.width_cells or cell_y >= self.height_cells:
            return None
        return cell_x, cell_y

    def inflate_cells(self, occupied_cells):
        inflated = set()
        radius_sq = self.inflation_radius_cells * self.inflation_radius_cells

        for cell_x, cell_y in occupied_cells:
            for dx in range(-self.inflation_radius_cells, self.inflation_radius_cells + 1):
                for dy in range(-self.inflation_radius_cells, self.inflation_radius_cells + 1):
                    if dx * dx + dy * dy > radius_sq:
                        continue

                    nx = cell_x + dx
                    ny = cell_y + dy
                    if 0 <= nx < self.width_cells and 0 <= ny < self.height_cells:
                        inflated.add((nx, ny))

        return inflated
