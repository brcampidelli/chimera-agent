/** The id of the built-in environment: the machine the app runs on, as it always was.
 *
 *  Its own module and not an export of `api.ts`: screens read it while rendering, and a test that
 *  mocks the API module wholesale must not take a constant down with the functions it replaced. */
export const LOCAL_ENVIRONMENT = "local";
