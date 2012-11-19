import itertools
import random
import collections
import fractions

import constants

functions = {}

def function(fn):
    functions[fn.__name__] = fn
    return fn

@function
def square(world, start_coord, direction=None):
    visible = neighbourhood(start_coord, n=3)
    return visible

@function
def cone(world, coord, direction=None):
    visible = set()
    # The square you are in is always visible as well one square
    # behind you
    visible.add(coord)

    main_direction = constants.DIFFS[direction]
    behind_you_direction = main_direction[0] * -1, main_direction[1] * -1

    behind_you = (coord[0] + behind_you_direction[0],
                  coord[1] + behind_you_direction[1])

    if behind_you in world:
        visible.add(behind_you)


    # First, everything in the direction the player is looking
    # straight ahead

    def look_until_wall(start, diff):
        coord = start
        v = set()

        running = True
        while running:
            coord = coord[0] + diff[0], coord[1] + diff[1]
            if coord not in world:
                break
            v.add(coord)
            objects = world[coord]
            for o in objects:
                if o[0] in constants.OPAQUE_OBJECTS:
                    running = False
        return v

    for direction in constants.ADJACENT[direction]:
        visible.update(look_until_wall(coord,
                                       constants.DIFFS[direction]))

    return visible

@function
def all(world, coord, direction=None):
    visible_coords = set(world)
    return visible_coords

@function
def rays(world, coord, direction=None):
    class Transform2D(object):
        def __init__(self):
            self.m00 = 1
            self.m01 = 0
            self.m10 = 0
            self.m11 = 1
            self.tX = 0
            self.tY = 0

        @classmethod
        def translate(cls, x, y):
            instance = cls()
            instance.tX = x
            instance.tY = y
            return instance

        @classmethod
        def linear(cls, m00, m01, m10, m11):
            instance = cls()
            instance.m00 = m00
            instance.m01 = m01
            instance.m10 = m10
            instance.m11 = m11
            return instance

        def apply(self, x, y):
            return x*self.m00 + y*self.m01 + self.tX, x*self.m10 + y*self.m11 + self.tY

        def __mul__(self, x):
            new_instance = self.__class__()
            new_instance.m00 = self.m00*x.m00 + self.m01*x.m10
            new_instance.m01 = self.m00*x.m01 + self.m01*x.m11
            new_instance.m10 = self.m10*x.m00 + self.m11*x.m10
            new_instance.m11 = self.m10*x.m01 + self.m11*x.m11
            new_instance.tX = self.tX + x.tX*self.m00 + x.tY*self.m01
            new_instance.tY = self.tY + x.tX*self.m10 + x.tY*self.m11
            return new_instance

        def inverse(self):
            determinant = self.m00*self.m11 - self.m01*self.m10
            new_instance = self.__class__()
            new_instance.m00 = self.m11/determinant
            new_instance.m01 = -self.m01/determinant
            new_instance.m10 = -self.m10/determinant
            new_instance.m11 = self.m00/determinant
            new_instance.tX = (self.tY*self.m01 - self.tX*self.m11)/determinant
            new_instance.tY = -(self.tY*self.m00 - self.tX*self.m10)/determinant
            return new_instance

        def __str__(self):
            return '[[{0} {1} {4}] [{2} {3} {5}]]'.format(*(self.m00, self.m01, self.m10, self.m11, self.tX, self.tY))

    def bresenham_line(a, b):
        yield a
        x0, y0 = a
        x1, y1 = b
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx - dy

        while (x0, y0) != (x1, y1):
            e2 = 2*error
            if e2 > -dy:
                error -= dy
                x0 += sx
            if e2 < dx:
                error += dx
                y0 += sy
            yield x0, y0

    # Optimisation: track whether blocks are impeded
    impedances = {}
    def is_impeded(point):
        previous_impedance = impedances.get(point)
        if previous_impedance is not None:
            return previous_impedance
        x, y = point
        impeded = any(obj[0] in constants.OPAQUE_OBJECTS
                          for obj
                          in world.get(camera_to_world.apply(x, y), ()))
        impedances[point] = impeded
        return impeded

    def neighbours(point):
        x, y = point
        yield x + 1, y
        yield x - 1, y
        yield x, y + 1
        yield x, y - 1

    def right_points_by_distance(max_radius):
        yield 0, 0
        half_radius = max_radius // 2
        for n in xrange(1, max_radius + 1):
            # Handle the on-axis cases
            yield 0, n
            yield n, 0
            yield 0, -n
            for i in xrange(1, half_radius):
                yield i, n
                yield n, i
                yield n, -i
                yield i, -n
            # Handle the corners
            yield half_radius, n
            yield half_radius, -n

    # Parameters
    MAX_RADIUS = 60
    Y_RADIUS_SCALE = 3
    APPROXIMATION_ACCURACY = 3
    # End of parameters
    MAX_RADIUS_SQUARED = MAX_RADIUS*MAX_RADIUS
    coord_matrix = Transform2D.translate(-coord[0], -coord[1])
    rotate_matrix = {constants.RIGHT: Transform2D.linear(1, 0, 0, 1),
                     constants.LEFT:  Transform2D.linear(-1, 0, 0, -1),
                     constants.UP:    Transform2D.linear(0, -1, 1, 0),
                     constants.DOWN:  Transform2D.linear(0, 1, -1, 0)}[direction]
    world_to_camera = rotate_matrix * coord_matrix
    camera_to_world = world_to_camera.inverse()
    outputs = [camera_to_world.apply(x, y) for x in (0, -1) for y in (-1, 0, 1)]
    # Scale x and y so it 'looks right' - compensating for characters being taller than wide
    x_scale = Y_RADIUS_SCALE if direction in (constants.UP, constants.DOWN) else 1
    y_scale = Y_RADIUS_SCALE if direction in (constants.LEFT, constants.RIGHT) else 1
    # Optimisation: keep track of blocked directions
    blocked_directions = {}
    potential_corners = []
    for x, y in right_points_by_distance(MAX_RADIUS):
        radius_squared = x_scale*x*x + y_scale*y*y
        # Peripheral vision limits
        if y == 0 and x > MAX_RADIUS:
            continue
        elif y != 0:
            # Early exit: discard any points outside the maximum radius
            local_max_radius = abs(float(x)**0.3/float(y))
            local_max_radius = local_max_radius/(1 + local_max_radius)
            local_max_radius *= MAX_RADIUS
            if radius_squared > local_max_radius * local_max_radius:
                continue
        direction_fraction = fractions.Fraction(x, y).limit_denominator(APPROXIMATION_ACCURACY) if y != 0 else None
        block_distance_squared = blocked_directions.get(direction_fraction, float('inf'))
        if radius_squared > block_distance_squared:
            continue
        # Use bresenham
        is_visible = True
        for point in bresenham_line((0, 0), (x, y)):
            if point in ((0, 0), (x, y)):
                continue
            if is_impeded(point):
                is_visible = False
                break
        if is_visible:
            outputs.append(camera_to_world.apply(x, y))
        # Handle the - ah-hah - corner case
        elif is_impeded((x, y)) and y:
            # Determine if this is a corner
            if len([point for point in neighbours((x, y))
                              if is_impeded(point)]) in (1, 2, 3):
                # At least 2 wall nearby, this is a corner or edge
                potential_corners.append((x, y))
        else:
            blocked_directions[direction_fraction] = radius_squared

    # Add potential corners
    for x, y in potential_corners:
        if len([point for point in neighbours((x, y))
                          if camera_to_world.apply(*point) in outputs]) >= 2:
            outputs.append(camera_to_world.apply(x, y))
    return set(outputs) & set(world.iterkeys())

@function
def blind(world, coord, direction):
    return set()
