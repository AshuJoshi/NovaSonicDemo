// // chromeextension/background.js

// chrome.action.onClicked.addListener(async (tab) => {
//   // When the extension icon is clicked, toggle the side panel for the current tab.
//   const currentWindowState = await chrome.windows.getCurrent();
//   if (currentWindowState.id) { // Ensure we have a window ID
//     await chrome.sidePanel.open({ windowId: currentWindowState.id });
//   } else {
//     console.error("Could not get current window ID to open side panel.");
//   }
// });

// // Optional: Set rules for when the side panel should be enabled.
// // For example, enable it on all pages or specific pages.
// // This allows the side panel to be opened contextually without needing the action button always.
// // chrome.runtime.onInstalled.addListener(() => {
// //   chrome.sidePanel.setOptions({
// //     // path: 'index.html', // Already set by default_path
// //     enabled: true // Enable on all tabs by default
// //   });
// // });


// chromeextension/background.js
chrome.action.onClicked.addListener(async (tab) => {
  if (tab && tab.id) {
    console.log(`Action clicked on tab ID: ${tab.id}. Opening side panel.`);
    try {
      await chrome.sidePanel.open({ tabId: tab.id });
      // You can also set the panel behavior here if needed,
      // for example, to ensure it uses your specific index.html,
      // though default_path in manifest should handle it.
      // await chrome.sidePanel.setOptions({
      //   tabId: tab.id,
      //   path: 'index.html',
      //   enabled: true
      // });
      console.log(`Side panel should be open for tab ID: ${tab.id}`);
    } catch (e) {
      console.error("Error opening side panel:", e);
    }
  } else {
    console.error("Action clicked, but no valid tab ID found. Tab object:", tab);
    // Fallback: Try opening for the current window if tab ID is missing for some reason
    // (though 'tab' from onClicked should usually have an ID)
    try {
        const currentWindow = await chrome.windows.getCurrent();
        if (currentWindow.id) {
            console.log("Attempting to open side panel for current window as fallback.");
            await chrome.sidePanel.open({ windowId: currentWindow.id });
        }
    } catch (e) {
        console.error("Error opening side panel for window (fallback):", e);
    }
  }
});