from typing import List

import re


def parse_music(music_string: str) -> List[int]:
    """ Input to this function is a string representing musical notes in a special ASCII format.
    Your task is to parse this string and return list of integers corresponding to how many beats does each
    not last.

    Here is a legend:
    'o' - whole note, lasts four beats
    'o|' - half note, lasts two beats
    '.|' - quater note, lasts one beat

    >>> parse_music('o o| .| o| o| .| .| .| .| o o')
    [4, 2, 1, 2, 2, 1, 1, 1, 1, 4, 4]
    """
    note_map = {'o': 4, 'o|': 2, '.|': 1}
    beats: List[int] = []
    for token in music_string.split():
        if token in note_map:
            beats.append(note_map[token])
            continue
        # Tolerate notes that are not separated by whitespace, e.g. 'o|.|'.
        if re.fullmatch(r'(?:o\||\.\||o)+', token):
            for note in re.findall(r'o\||\.\||o', token):
                beats.append(note_map[note])
        else:
            raise KeyError(token)
    return beats

