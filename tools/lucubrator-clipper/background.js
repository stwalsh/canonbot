const DROP_URL = "http://localhost:3847/drop";

chrome.action.onClicked.addListener(async (tab) => {
  // Get selected text from the page
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const sel = window.getSelection().toString().trim();
        const meta = document.querySelector('meta[name="description"]');
        return {
          selection: sel,
          description: meta ? meta.content : "",
        };
      },
    });
  } catch (e) {
    // Can't inject into chrome:// pages etc
    console.error("Clipper: can't access page", e);
    return;
  }

  const { selection, description } = results[0].result;
  const text = selection || description || "";
  const title = tab.title || "";
  const url = tab.url || "";

  try {
    const resp = await fetch(DROP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, url, text }),
    });

    if (resp.ok) {
      // Brief green badge to confirm
      chrome.action.setBadgeText({ text: "✓", tabId: tab.id });
      chrome.action.setBadgeBackgroundColor({ color: "#4a9" });
      setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 1500);
    } else {
      chrome.action.setBadgeText({ text: "!", tabId: tab.id });
      chrome.action.setBadgeBackgroundColor({ color: "#c44" });
      setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 3000);
    }
  } catch (e) {
    // Server not running
    chrome.action.setBadgeText({ text: "!", tabId: tab.id });
    chrome.action.setBadgeBackgroundColor({ color: "#c44" });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 3000);
    console.error("Clipper: drop server not reachable", e);
  }
});
