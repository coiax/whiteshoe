from __future__ import print_function
import random
import operator

import constants
import utility
import itertools


generators = {}

def generator(fn):
    generators[fn.__name__] = fn
    return fn

@generator
def purerandom(X=80,Y=24,seed=0):
    world = {}
    r = random.Random(seed)

    for i,j in itertools.product(range(X), range(Y)):
        if r.random() < 0.35:
            world[i,j] = [(constants.OBJ_WALL, {})]
        else:
            world[i,j] = [(constants.OBJ_EMPTY, {})]

    return world

@generator
def empty(X=80,Y=24,seed=None):
    world = {}

    for i,j in itertools.product(range(X), range(Y)):
        world[i,j] = [(constants.OBJ_EMPTY, {})]
    return world

@generator
def ca_maze(X=80,Y=24,seed=1):
    ca_world = utility.CellularAutomaton(X, Y)
    r = random.Random(seed)
    ca_world.seed(0.35,rng=r)
    ca_world.converge('3/12345')

    return utility.ca_world_to_world(ca_world)

@generator
def ca_caves(X=80,Y=24,seed=1):
    ca_world = utility.CellularAutomaton(X, Y)
    r = random.Random(seed)
    ca_world.seed(0.5, rng=r)
    ca_world.converge('678/345678', boundary = True)

    # Now the maze CA tends to generate isolated islands
    return utility.ca_world_to_world(ca_world)

@generator
def depth_first(X=80, Y=24, seed=0):
    r = random.Random(seed)

    # The division by 2 will be important later
    cells = list(itertools.product(range(X//2), range(Y//2)))

    initial_cell = r.choice(cells)
    current_cell = initial_cell

    visited = set()
    visited.add(current_cell)

    removed_walls = set()

    stack = []

    while set(cells) - visited:
        neighbours = set(utility.cardinal_neighbourhood(current_cell))
        neighbours &= set(cells)

        # If the current cell has any neighbours which have not been visited
        if neighbours - visited:
            # Choose random one of the unvisited neighbours
            neighbour = r.choice(list(neighbours - visited))
            # Push the current cell to the stack
            stack.append(current_cell)
            # Remove the wall between the current cell and the chosen cell
            removed_walls.add((neighbour, current_cell))
            # Make the chosen cell the current cell and mark it as visited
            current_cell = neighbour
            visited.add(current_cell)
        elif stack:
            current_cell = stack.pop()
        else:
            current_cell = r.choice(cells)
            visited.add(current_cell)

    # Now we have a number of eliminated walls
    world = {}

    for x,y in itertools.product(range(X), range(Y)):
        world[x,y] = [(constants.OBJ_WALL, {})]

    for point_a, point_b in removed_walls:
        real_a = (point_a[0] * 2, point_a[1] * 2)
        real_b = (point_b[0] * 2, point_b[1] * 2)

        # The removed wall is the shared neighbourhood between them
        shared = set(utility.cardinal_neighbourhood(real_a))
        shared &= set(utility.neighbourhood(real_b))

        assert len(shared) == 1

        world[shared.pop()] = [(constants.OBJ_EMPTY, {})]
        world[real_a] = [(constants.OBJ_EMPTY, {})]
        world[real_b] = [(constants.OBJ_EMPTY, {})]

    return world

@generator
def dungeon_alpha(X=69,Y=16, seed=0, num_rooms=6):
    r = random.Random(seed)
    MIN_SIZE = 4
    MAX_SIZE = 9
    NUM_ROOMS = num_rooms
    rooms_generated = 0

    level = {}
    for x in range(X):
        for y in range(Y):
            level[x,y] = "rock"

    while rooms_generated < NUM_ROOMS:
        x_length = r.randint(MIN_SIZE, MAX_SIZE)
        y_length = r.randint(MIN_SIZE, MAX_SIZE)

        # topleft
        x1 = r.randint(0,X-MIN_SIZE)
        y1 = r.randint(0,Y-MIN_SIZE)

        coordinates = set()

        for x in range(x1, x1 + x_length):
            for y in range(y1, y1 + y_length):
                # The min() is to ensure the coordinates are not
                # set and checked outside the boundries of the generated
                # level.
                coordinates.add((min(x,X-1),min(y,Y-1)))

        perimeter = utility.perimeter(coordinates)
        valid = True
        adjacent = False
        for coord in perimeter:
            if level[coord] not in ("rock", "smoothwall"):
                valid = False
                break

        for coord in utility.border(perimeter):
            if coord in level and level[coord] != "rock":
                valid = False
                break

        if valid:
            for coord in perimeter:
                level[coord] = "smoothwall"
            for coord in set(coordinates) - set(perimeter):
                level[coord] = "floor"

            rooms_generated += 1

    new_level = {}

    for coord, entity_type in list(level.items()):
        entity_id = utility.get_id('entity')
        new_level[coord + (0,)] = [(entity_id, entity_type)]

    return new_level

def world_to_string(world):
    rows = []

    coords = sorted(list(world.keys()),key=operator.itemgetter(1,0))

    last_y = None

    row = ''
    for coord in coords:
        x, y = coord
        if last_y is None:
            pass
        else:
            if y > last_y:
                rows.append(row)
                row = ''

        last_y = y

        char = ' '
        if world[coord]:
            last_object = world[coord][-1]
            objtag, attr = last_object
            char = constants.DISPLAY_CHAR.get(objtag, '?')
        row += char

    return '\n'.join(rows)

def print_level(level):
    coords = sorted(list(level),key=operator.itemgetter(1,0))
    last_y = None
    for coord in coords:
        y = coord[1]
        if last_y is None:
            pass
        elif last_y < y:
            print()

        last_y = y
        entity = level[coord][-1]
        entity_id, entity_type = entity
        if entity_type == "smoothwall":
            print('x',end='')
        elif entity_type == "roughwall":
            print('#',end='')
        elif entity_type == "rock":
            print(' ',end='')
        elif entity_type == "floor":
            print('.',end='')
    print()


def pretty_walls(world):
    for coord, objects in world.items():
        if objects[0][0] == constants.OBJ_EMPTY:
            continue

        vertical = False
        horizontal = False

        for neighbour in ((coord[0], coord[1] - 1), (coord[0], coord[1] + 1)):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != constants.OBJ_EMPTY:
                vertical = True
                break

        for neighbour in ((coord[0] - 1, coord[1]), (coord[0] + 1, coord[1])):
            if neighbour not in world:
                continue
            if world[neighbour][0][0] != constants.OBJ_EMPTY:
                horizontal = True
                break

        if not vertical and not horizontal:
            # Do nothing
            continue
        elif vertical and not horizontal:
            del objects[0]
            objects.append((constants.OBJ_VERTICAL_WALL, {}))
        elif not vertical and horizontal:
            del objects[0]
            objects.append((constants.OBJ_HORIZONTAL_WALL, {}))
        elif vertical and horizontal:
            del objects[0]
            objects.append((constants.OBJ_CORNER_WALL, {}))

    return world
