"""mayari_pygments.py — a custom Pygments style for the "Mayari" dark theme.

Mayari is the Filipino moon goddess — the mythological sibling of Apolaki, the sun
(the theme of the companion Apigee course). Where Apolaki is warm solar gold and
ember, Mayari is a cool moonlit night: midnight indigo lit by lunar teal, amethyst,
and silver-blue, with coral reserved for errors. Tuned for readability on the code
background #0E1119 and to harmonise with the site's CSS variables (assets/style.css).

build.py renders this to docs/assets/pygments.css via:
    HtmlFormatter(style=MayariStyle).get_style_defs(".codehilite")
so highlighting is class-based; the colours below become the stylesheet. The course's
code is mostly Java, YAML (Kubernetes / Envoy AI Gateway CRDs), JSON, and bash.
"""

from pygments.style import Style
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Literal,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Token,
    Whitespace,
)

# ---- Mayari palette -----------------------------------------------------------
BG = "#0E1119"          # midnight code background
FG = "#E8ECF4"          # primary text — moonlight
TEAL = "#4FE3C1"        # lunar teal — keywords-as-tags, YAML keys
TEAL_SOFT = "#7FF0D6"   # soft teal — functions
AMETHYST = "#A98BFF"    # amethyst — Java keywords, decorators
AMETHYST_SOFT = "#C4AEFF"
SILVER = "#8FB3FF"      # silver-blue — classes, attributes
SKY = "#79C0FF"         # cool blue — numbers, constants
GREEN = "#8FE3A8"       # moonlit green — strings
GREEN_SOFT = "#B6F0C4"  # string escapes
CORAL = "#FF6B6B"       # coral — errors
AMBER = "#F5B544"       # amber — builtins, warnings
MUTED = "#6B7488"       # slate — comments, punctuation


class MayariStyle(Style):
    """Moonlit highlighting for Java, YAML, JSON, and bash on midnight indigo."""

    name = "mayari"
    background_color = BG
    highlight_color = "#1E2433"
    line_number_color = MUTED
    line_number_background_color = BG

    styles = {
        Token: FG,
        Text: FG,
        Whitespace: "",
        Error: f"bold {CORAL}",

        Comment: f"italic {MUTED}",
        Comment.Preproc: AMETHYST,
        Comment.Special: f"italic bold {MUTED}",

        Keyword: f"bold {AMETHYST}",
        Keyword.Constant: SKY,
        Keyword.Declaration: f"bold {AMETHYST}",
        Keyword.Namespace: f"bold {TEAL}",
        Keyword.Type: SILVER,

        Operator: SILVER,
        Operator.Word: f"bold {AMETHYST}",
        Punctuation: "#AEB6C7",

        Name: FG,
        Name.Attribute: SILVER,
        Name.Builtin: AMBER,
        Name.Builtin.Pseudo: AMBER,
        Name.Class: f"bold {SILVER}",
        Name.Constant: SKY,
        Name.Decorator: AMETHYST_SOFT,
        Name.Entity: GREEN_SOFT,
        Name.Exception: f"bold {CORAL}",
        Name.Function: TEAL_SOFT,
        Name.Label: TEAL,
        Name.Namespace: SILVER,
        Name.Tag: f"bold {TEAL}",
        Name.Variable: FG,
        Name.Variable.Class: SILVER,
        Name.Variable.Instance: FG,

        Number: SKY,
        Literal: SKY,
        Literal.Date: GREEN,

        String: GREEN,
        String.Backtick: GREEN_SOFT,
        String.Char: GREEN_SOFT,
        String.Doc: f"italic {MUTED}",
        String.Double: GREEN,
        String.Escape: f"bold {GREEN_SOFT}",
        String.Interpol: f"bold {TEAL_SOFT}",
        String.Regex: TEAL,
        String.Single: GREEN,
        String.Symbol: SKY,

        Generic.Deleted: CORAL,
        Generic.Emph: "italic",
        Generic.Error: CORAL,
        Generic.Heading: f"bold {FG}",
        Generic.Inserted: GREEN,
        Generic.Output: MUTED,
        Generic.Prompt: f"bold {TEAL}",
        Generic.Strong: "bold",
        Generic.Subheading: f"bold {SILVER}",
        Generic.Traceback: CORAL,
    }
