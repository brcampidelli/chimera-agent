"""The 30 briefs, frozen by PREREGISTRATION.md before any paid call.

Each brief is a product, the screens a design of it would have (3, 4 or 5, in a fixed order) and a
design system seed. A brief is a short, ordinary request: what an owner would type, in English so
the measure does not depend on the model's translation. Ten designs have 3 screens, ten have 4 and
ten have 5, because the registered claim is about designs of three or more screens.

The refinement request of each design is derived in :func:`refinement` from the brief's index alone,
so the corpus has no free choice left once this file is committed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brief:
    id: str
    brief: str
    screens: tuple[str, ...]


def _b(i: int, brief: str, *screens: str) -> Brief:
    return Brief(f"b{i:02d}", brief, tuple(screens))


BRIEFS: tuple[Brief, ...] = (
    # --- 3 screens -------------------------------------------------------------------------------
    _b(1, "A landing page for a small-batch coffee roaster that ships beans monthly.", "Home", "Plans", "About"),
    _b(2, "A sign-up flow for a language-learning app aimed at busy adults.", "Welcome", "Sign up", "Goals"),
    _b(3, "A dashboard for a freelance invoicing tool.", "Overview", "Invoices", "Clients"),
    _b(4, "A booking site for a neighbourhood yoga studio.", "Home", "Schedule", "Pricing"),
    _b(5, "A portfolio for an architecture student.", "Intro", "Projects", "Contact"),
    _b(6, "A checkout experience for an independent bookshop.", "Cart", "Shipping", "Payment"),
    _b(7, "A status page for a developer API company.", "Status", "Incidents", "Subscribe"),
    _b(8, "A recipe app for weeknight dinners.", "Discover", "Recipe", "Shopping list"),
    _b(9, "A pet adoption website for a city shelter.", "Home", "Dogs", "Apply"),
    _b(10, "A product tour for a team note-taking tool.", "Hero", "Features", "Pricing"),
    # --- 4 screens -------------------------------------------------------------------------------
    _b(11, "A mobile banking app for teenagers with parental controls.", "Home", "Spending", "Savings goals", "Parent view"),
    _b(12, "A conference website for a one-day design event.", "Home", "Speakers", "Schedule", "Tickets"),
    _b(13, "A bike-sharing app for a mid-sized city.", "Map", "Ride", "Wallet", "Help"),
    _b(14, "A learning platform for high-school chemistry.", "Courses", "Lesson", "Quiz", "Progress"),
    _b(15, "A property rental marketplace for short stays.", "Search", "Listing", "Booking", "Host"),
    _b(16, "A fitness tracker companion for runners.", "Today", "Runs", "Plans", "Profile"),
    _b(17, "A subscription box for houseplants.", "Home", "Boxes", "Care guide", "Account"),
    _b(18, "A food-truck ordering site for lunchtime pickup.", "Menu", "Order", "Trucks today", "Rewards"),
    _b(19, "A job board for remote designers.", "Jobs", "Job detail", "Companies", "Post a job"),
    _b(20, "An event ticketing app for small music venues.", "Events", "Event", "Tickets", "Venue"),
    # --- 5 screens -------------------------------------------------------------------------------
    _b(21, "A telehealth app for routine check-ups.", "Home", "Book visit", "Waiting room", "Records", "Billing"),
    _b(22, "A project management tool for small construction firms.", "Projects", "Timeline", "Budget", "Team", "Reports"),
    _b(23, "An online marketplace for second-hand camera gear.", "Browse", "Listing", "Sell", "Messages", "Orders"),
    _b(24, "A music-learning app for piano beginners.", "Home", "Lesson", "Practice", "Songs", "Progress"),
    _b(25, "A smart-home control app for apartments.", "Rooms", "Devices", "Energy", "Scenes", "Settings"),
    _b(26, "A museum visit planner with timed tickets.", "Visit", "Exhibitions", "Tickets", "Map", "Membership"),
    _b(27, "A customer support portal for a software company.", "Help centre", "Article", "Contact", "My tickets", "Status"),
    _b(28, "A grocery delivery service for older adults.", "Home", "Categories", "Basket", "Delivery slots", "Help"),
    _b(29, "A travel itinerary planner for group trips.", "Trips", "Itinerary", "Budget", "Group", "Documents"),
    _b(30, "A nonprofit donation site for a river clean-up.", "Home", "Donate", "Impact", "Volunteer", "Stories"),
)
assert len(BRIEFS) == 30 and len({b.id for b in BRIEFS}) == 30
assert [len(b.screens) for b in BRIEFS] == [3] * 10 + [4] * 10 + [5] * 10

#: The six requests, applied in turn. Each asks for a small change of the kind ``artboard.edit_html``
#: exists for; none names a selector, an index or a source fragment.
REQUESTS: tuple[str, ...] = (
    "make the main headline larger and give it the accent colour",
    "change the label of the primary button to \"Continue\"",
    "increase the vertical spacing between its sections",
    "replace the first paragraph with a shorter one of at most 15 words",
    "give the cards or boxes a larger border radius",
    "make the secondary text darker so it is easier to read",
)
_ORDINALS = ("second", "third")


def refinement(index: int) -> tuple[str, int, str]:
    """``(request text, target screen position, how the screen is named)`` for brief ``index`` (0-based).

    Two of every three requests name the screen; the third refers to it by its place in the list
    ("the last screen", "the second screen"), so the model has to read the list to find it. The
    target rotates through the screens so it is not always the first.
    """
    brief = BRIEFS[index]
    n = len(brief.screens)
    ordinal = index % 3 == 2
    if ordinal:
        position = n - 1 if index % 2 == 0 else 1
        place = "last" if position == n - 1 else _ORDINALS[0]
        how = "ordinal"
        head = f"On the {place} screen of the design"
    else:
        position = (index + 1) % n
        how = "named"
        head = f"On the \"{brief.screens[position]}\" screen"
    return f"{head}, {REQUESTS[index % len(REQUESTS)]}.", position, how
