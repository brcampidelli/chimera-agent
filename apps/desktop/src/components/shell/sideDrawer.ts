import { createContext, useContext } from "react";

/** What a control inside the shell needs to know about the right panel when it is a drawer.
 *
 *  `drawer` is true below the drawer breakpoint, where the panel is a dialog and the layout's `mode`
 *  does not describe whether it is on screen. `open` says whether it is, and `toggle` opens or closes
 *  it the same way its edge tab does. Outside a shell (tests, other hosts) there is no value. */
export interface SideDrawerState {
  drawer: boolean;
  open: boolean;
  toggle: () => void;
}

export const SideDrawerContext = createContext<SideDrawerState | null>(null);

export function useSideDrawer(): SideDrawerState | null {
  return useContext(SideDrawerContext);
}
