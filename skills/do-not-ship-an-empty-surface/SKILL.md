---
name: do-not-ship-an-empty-surface
description: An empty screen, tab or category is a promise the product is not keeping. Hide it until it has content, or do not build it yet.
version: 0.1.0
kind: anti_pattern
triggers:
- adding a tab with no data
- the category has no posts
- placeholder screen
- coming soon
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to ship a navigable destination that will be empty for most users: a tab with no
rows, a category with no entries, a dashboard whose data source is not wired up, a page that says
"coming soon".

It does not apply to a surface that is empty because the *user* has not done anything yet. A task
list with no tasks is correct, and should say how to add one.

## Do

1. Decide which kind of empty it is. Empty-because-the-user-is-new needs a good empty state with
   the next action in it. Empty-because-we-have-not-built-it needs to not be there.
2. For the second kind: do not render the destination at all. Generate the route only when there is
   something behind it.
3. If it must exist for development, gate it on a development flag so an installed build cannot
   reach it.
4. Where the emptiness is genuinely the news — a hub with one entry, a feed just started — say so
   in words, and make the invitation to contribute the main content of the page.

## Avoid

"Coming soon", "No data yet" with no explanation, and a nav entry that leads to a blank column.
Each one costs the reader a click and returns nothing, and after two of them they stop trusting the
navigation — a cost paid by every other page in the product.

Avoid also the version that looks fine: a category page that renders correctly with zero items. It
does not look broken, so nobody reports it, and it quietly says the project abandoned that topic.

## Check

Load the surface with an empty data source and read what a first-time user would see. If the answer
is a heading and whitespace, it should not have shipped.

For generated routes the check is mechanical: assert that the route list is derived from the
content, so a category with no entries produces no page at all.

## Risk

Hiding a destination until it has content makes navigation shift as data arrives, which can be
disorienting — a menu that grows between visits needs a reason the user can infer.

This can also be taken too far, into hiding features that are merely unpopular. The line is whether
the surface can ever show something: unbuilt is hidden, unused is shown with a good empty state.
