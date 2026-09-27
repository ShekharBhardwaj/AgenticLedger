"""
Icon and color marks for labels: the picker's two lists, server side.

A run or session can carry an icon and a color on its label row so a user
can tell their loops apart at a glance. These tuples are the source of
truth for what the API accepts; the dashboard's picker
(dashboard-app/src/LabelMarks.tsx) lists the same names in the same order,
and tests/test_labels.py holds the two in step. Order matters: it is the
order the picker draws them in.
"""

LABEL_COLORS = (
    "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink",
)

LABEL_ICONS = (
    "folder", "dollar", "book", "graduation-cap", "pencil", "pen-tool",
    "braces", "terminal", "music", "popcorn", "wand", "palette",
    "stethoscope", "asterisk", "flower", "briefcase", "chart", "kettlebell",
    "dumbbell", "notebook", "scale", "globe-stand", "plane", "globe",
    "wrench", "paw", "flask", "brain", "heart", "plant",
)
