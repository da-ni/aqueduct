from enum import Enum, unique


# Each value is the inner markup of a 24x24 stroked SVG, copied from the redesign mockup.
# @unique because two members with the same markup would silently become aliases.
@unique
class Icon(Enum):
    OVERVIEW = '<path d="M5 20V11M12 20V4M19 20v-6M3 20h18"/>'
    MODELS = (
        '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/>'
        '<path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>'
    )
    MCP = '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0V8zM12 17v4"/>'
    KEY = '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M14 9l2 2"/>'
    REQUESTS = '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>'
    TEAMS = (
        '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6'
        'M16 4.6a3.5 3.5 0 010 6.8M18 14.3c2.1.7 3.5 2.6 3.5 5.7"/>'
    )
    FILE = (
        '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z"/>'
        '<path d="M14 3v5h5"/>'
    )
    BATCH = '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>'
    VECTOR_STORE = (
        '<ellipse cx="12" cy="6" rx="8" ry="3"/>'
        '<path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>'
    )
    ORGANIZATION = (
        '<path d="M4 21V5a1 1 0 011-1h9a1 1 0 011 1v16M15 10h4a1 1 0 011 1v10M3 21h18'
        'M8 8h3M8 12h3M8 16h3"/>'
    )
    SETTINGS = (
        '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/>'
        '<circle cx="9" cy="17" r="2"/>'
    )
    ADMIN = '<path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4"/>'
    PROFILING = '<path d="M3 12h4l3-7 4 14 3-7h4"/>'
    LINK = (
        '<path d="M10 14a4 4 0 005.66 0l3-3a4 4 0 00-5.66-5.66l-1 1'
        'M14 10a4 4 0 00-5.66 0l-3 3a4 4 0 005.66 5.66l1-1"/>'
    )
    EXTERNAL = (
        '<path d="M14 5h5v5M19 5l-8 8M11 7H6a1 1 0 00-1 1v10a1 1 0 001 1h10a1 1 0 001-1v-5"/>'
    )
    MENU = '<path d="M4 7h16M4 12h16M4 17h16"/>'
    COPY = '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>'
    SUN = (
        '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2'
        'M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4"/>'
    )
    MOON = '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>'
    USER = '<circle cx="12" cy="9" r="4"/><path d="M4.5 20.5c1-3.4 4-5.5 7.5-5.5s6.5 2.1 7.5 5.5"/>'
    SIGN_OUT = '<path d="M10 4H6a2 2 0 00-2 2v12a2 2 0 002 2h4M15 8l4 4-4 4M19 12H9"/>'
