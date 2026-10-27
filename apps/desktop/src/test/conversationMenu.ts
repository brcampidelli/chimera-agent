import { screen } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";

/** Opens the conversation's "⋯" menu and returns the item with that name.
 *
 *  The header's actions (export, open in a window, clear, share) live in that menu, so they are not
 *  in the tree until it is opened, the same as a person would have to do. */
export async function openConversationMenuItem(user: UserEvent, name: string | RegExp): Promise<HTMLElement> {
  await user.click(await screen.findByTestId("conversation-menu"));
  return screen.findByRole("menuitem", { name });
}
