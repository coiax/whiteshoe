import os
import os.path
import operator
import re
import logging
logger = logging.getLogger(__name__)

import utility

def load_by_name(level_name):
    level_filename = level_name + ".level"
    # First up, find dat level.
    for root, dirs, files in os.walk('.'):
        if any(level_filename == filename for filename in files):
            level_path = os.path.join(root, level_filename)
            break

    else:
        raise Exception("Level {} not found.".format(level_name))

    with open(level_path) as f:
        level_string = f.read()
    return load_from_string(level_string)

def load_from_string(level_string):
    # Parse into d[x,y] -> '<symbol>'

    d = {}
    directives = []

    z = 0
    y = 0
    for row in level_string.split("\n"):
        # Skip empty lines.
        if not row:
            continue
        if row.startswith('#'):
            # This is a command line or comment.
            # Another # is a comment, anything else is a command/directive
            stripped = row[1:].strip()
            if stripped.startswith('#'):
                # It's a comment, ignore.
                pass
            # Then check for a z level directive.
            mo = re.match("Z (-?\d+)", stripped, re.IGNORECASE)
            if mo is not None:
                numberish = mo.group(1)
                try:
                    z = int(numberish)
                except ValueError:
                    fmt = "Bad Z directive argument: {}"
                    logging.error(fmt.format(numberish))

            else:
                directives.append(stripped)
            # Regardless of comment or directive, skip to the next line
            # note that this DOES NOT increment y, as it's supposed to
            continue

        for x, symbol in enumerate(row):
            d[x,y,z] = symbol
        y += 1

    # Bottom right symbol determines size.
    # TODO we should probably check if the various z levels are the same size?
    bottom_right = sorted(d, key=operator.itemgetter(1,0))[-1]

    # Probably need these for a sanity check.
    width = bottom_right[0] + 1
    height = bottom_right[1] + 1

    # Then turn our symbols into entities, work out which ones.
    # Entities that need additional state data, put that down as well

    level = {}
    entity_state = {}
    for coord in d:
        level[coord] = entities = []
        symbol = d[coord]

        entity_id = utility.get_id('entity')

        if symbol == '.':
            entity_type = 'floor'
        elif symbol == '}':
            entity_type = 'pool'
        elif symbol == ' ':
            entity_type = 'solidrock'
        elif symbol in ('<', '>'):
            # Add some floor for the stairs.
            floor_id = utility.get_id('entity')
            entities.append((floor_id,'floor'))

            entity_type = 'stair'
            if symbol == '<':
                entity_state[entity_id] = {'flags': ['up']}
            else:
                entity_state[entity_id] = {'flags': ['down']}
        else:
            msg = "Unrecognised map symbol {} at {}".format(symbol, coord)
            logger.warning(msg)
            entity_type = 'unknown'
        entities.append((entity_id, entity_type))

    return level, entity_state
