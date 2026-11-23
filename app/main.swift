// Manuscript Editor: a native macOS window around the local editor server.
// No browser and no network needed: it starts server.py (if not already
// running on 127.0.0.1:8377) and shows it in a WebKit window. Quitting stops
// the server if this app started it, and asks first if there are unsaved edits.
import Cocoa
import WebKit

let port = 8377
let base = "http://127.0.0.1:\(port)/"
let editorDir = Bundle.main.object(forInfoDictionaryKey: "EditorDir") as? String ?? ""
let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/manuscript-editor.log")

func serverUp() -> Bool {
    guard let url = URL(string: base + "api/papers") else { return false }
    var req = URLRequest(url: url); req.timeoutInterval = 1.5
    let sem = DispatchSemaphore(value: 0); var ok = false
    URLSession.shared.dataTask(with: req) { _, resp, _ in
        ok = (resp as? HTTPURLResponse)?.statusCode == 200; sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + 2)
    return ok
}

func findPython() -> String? {
    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
    where FileManager.default.isExecutableFile(atPath: p) { return p }
    return nil
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var reallyQuit = false

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()   // keeps the page's settings (name, split, last paper)
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1500, height: 950),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Manuscript Editor"
        window.contentView = web
        window.setFrameAutosaveName("MainWindow")
        if !window.setFrameUsingName("MainWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.loadHTMLString("<body style='font:15px -apple-system;color:#666;display:grid;place-items:center;height:90vh'>Starting the editor…</body>", baseURL: nil)

        DispatchQueue.global().async {
            if !serverUp() {
                if let err = self.startServer() { DispatchQueue.main.async { self.fatal(err) }; return }
                var up = false
                for _ in 0..<60 { if serverUp() { up = true; break }; Thread.sleep(forTimeInterval: 0.2) }
                if !up { DispatchQueue.main.async { self.fatal("The editor server did not start. See ~/Library/Logs/manuscript-editor.log") }; return }
            }
            DispatchQueue.main.async { self.web.load(URLRequest(url: URL(string: base)!)) }
        }
    }

    func startServer() -> String? {
        let script = editorDir + "/server.py"
        guard FileManager.default.fileExists(atPath: script) else {
            return "Cannot find the editor at \(editorDir). Run app/build_app.sh again."
        }
        guard let py = findPython() else { return "python3 not found." }
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let log = try? FileHandle(forWritingTo: logURL); log?.seekToEndOfFile()
        log?.write("---- \(Date()) starting server\n".data(using: .utf8)!)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = [script, "--port", String(port), "--no-open"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/Library/TeX/texbin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env
        p.standardOutput = log; p.standardError = log
        do { try p.run() } catch { return "Could not start python3: \(error.localizedDescription)" }
        server = p
        return nil
    }

    func fatal(_ msg: String) {
        let a = NSAlert(); a.messageText = "Manuscript Editor"; a.informativeText = msg; a.alertStyle = .critical
        a.runModal(); reallyQuit = true; NSApp.terminate(nil)
    }

    // Links that leave the editor (none expected) open in the default browser, not here.
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler h: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, let host = u.host, host != "127.0.0.1", u.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(u); h(.cancel); return
        }
        h(.allow)
    }

    // The page uses alert/confirm/prompt; WebKit shows nothing unless we do.
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.runModal(); h()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        h(a.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText d: String?, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = p; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24)); field.stringValue = d ?? ""
        a.accessoryView = field; a.window.initialFirstResponder = field
        h(a.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
        if reallyQuit || web == nil { return .terminateNow }
        web.evaluateJavaScript("typeof dirtyList === 'function' ? dirtyList().length : 0") { r, _ in
            let n = (r as? Int) ?? 0
            var go = true
            if n > 0 {
                let a = NSAlert()
                a.messageText = "You have unsaved edits in \(n) paragraph\(n == 1 ? "" : "s")."
                a.informativeText = "Quit anyway and lose them? (Save with ⌘S first to keep them.)"
                a.addButton(withTitle: "Cancel"); a.addButton(withTitle: "Quit")
                go = a.runModal() == .alertSecondButtonReturn
            }
            if go { self.reallyQuit = true }
            NSApp.reply(toApplicationShouldTerminate: go)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ n: Notification) {
        if let p = server, p.isRunning { p.terminate(); p.waitUntilExit() }
    }

    @objc func reloadPage(_ s: Any?) { web.reload() }
    @objc func zoomIn(_ s: Any?) { web.pageZoom = min(web.pageZoom + 0.1, 3) }
    @objc func zoomOut(_ s: Any?) { web.pageZoom = max(web.pageZoom - 0.1, 0.5) }
    @objc func zoomReset(_ s: Any?) { web.pageZoom = 1 }

    func buildMenu() {
        let main = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let it = NSMenuItem(); let m = NSMenu(title: title); items.forEach(m.addItem); it.submenu = m; main.addItem(it)
        }
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        sub("Manuscript Editor", [
            item("Hide Manuscript Editor", #selector(NSApplication.hide(_:)), "h"),
            .separator(),
            item("Quit Manuscript Editor", #selector(NSApplication.terminate(_:)), "q"),
        ])
        sub("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Paste and Match Style", #selector(NSTextView.pasteAsPlainText(_:)), "v", [.command, .option, .shift]),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        sub("View", [
            item("Reload", #selector(reloadPage(_:)), "r"),
            .separator(),
            item("Zoom In", #selector(zoomIn(_:)), "+"),
            item("Zoom Out", #selector(zoomOut(_:)), "-"),
            item("Actual Size", #selector(zoomReset(_:)), "0"),
        ])
        sub("Window", [
            item("Minimise", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
