import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SharePanel } from "@/components/code/SharePanel";
import { getNetworkShare, listShares, openNetworkShare, shareSession } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const LINK = { token: "tok-1", session_id: "s1", created_at: 1, label: "Ana" };

/** The app's own client, not the test helper's: queries stay fresh for thirty seconds there
 *  (`main.tsx`), and that is the condition under which the panel showed a stale list live. */
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false, staleTime: 30_000 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <SharePanel sessionId="s1" presence={[]} onSharesChanged={() => {}} onClose={() => {}} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("the Share panel's list", () => {
  beforeEach(() => {
    vi.mocked(listShares).mockReset();
    vi.mocked(getNetworkShare).mockReset().mockResolvedValue({ open: false, port: null, urls: [] });
    vi.mocked(openNetworkShare).mockReset();
    vi.mocked(shareSession).mockReset();
  });

  it("re-reads the links from the server when the door opens, though the cached list is still fresh", async () => {
    vi.mocked(listShares).mockResolvedValueOnce({ shares: [{ ...LINK, url: null }] });
    mount();
    expect(await screen.findByText(/no address until/i)).toBeInTheDocument();

    // The door opens; the server now renders every link with an address.
    vi.mocked(openNetworkShare).mockResolvedValue({ open: true, port: 5000, urls: ["http://10.0.0.2:5000/"] });
    vi.mocked(getNetworkShare).mockResolvedValue({ open: true, port: 5000, urls: ["http://10.0.0.2:5000/"] });
    vi.mocked(listShares).mockResolvedValue({ shares: [{ ...LINK, url: "http://10.0.0.2:5000/?t=tok-1" }] });
    await userEvent.setup().click(screen.getByRole("button", { name: /open on this network/i }));

    await waitFor(() => expect(screen.getByTestId("share-door-open")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("http://10.0.0.2:5000/?t=tok-1")).toBeInTheDocument());
    expect(listShares).toHaveBeenCalledTimes(2);
  });

  it("shows a minted link at once, not the list from before it existed", async () => {
    vi.mocked(listShares).mockResolvedValueOnce({ shares: [] });
    mount();
    expect(await screen.findByText(/no links yet/i)).toBeInTheDocument();

    vi.mocked(shareSession).mockResolvedValue({ ...LINK, url: null });
    vi.mocked(listShares).mockResolvedValue({ shares: [{ ...LINK, url: null }] });
    await userEvent.setup().click(screen.getByRole("button", { name: /new link/i }));

    await waitFor(() => expect(screen.getByTestId("share-list")).toHaveTextContent("Ana"));
  });
});
