import Cocoa
import WebKit
final class Delegate: NSObject, NSApplicationDelegate, WKUIDelegate {
 var web: WKWebView!
 @objc func reloadPage(){web.reload()}
 func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
 let alert=NSAlert();alert.messageText=prompt;alert.addButton(withTitle:"保存");alert.addButton(withTitle:"取消");let input=NSTextField(frame:NSRect(x:0,y:0,width:400,height:24));input.stringValue=defaultText ?? "";alert.accessoryView=input;alert.beginSheetModal(for:window){response in completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil)}
 }
 var window:NSWindow!
 func applicationDidFinishLaunching(_ notification: Notification) {
  let menu=NSMenu();let appItem=NSMenuItem();menu.addItem(appItem);let sub=NSMenu();sub.addItem(withTitle:"退出管理窗口（服务继续运行）",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q");let refresh=NSMenuItem(title:"刷新页面",action:#selector(reloadPage),keyEquivalent:"r");refresh.target=self;sub.addItem(refresh);appItem.submenu=sub;NSApp.mainMenu=menu
  window=NSWindow(contentRect:NSRect(x:0,y:0,width:1440,height:930),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
  window.title="内网 IOPV · 运行管理";window.center();let view=WKWebView(frame:window.contentView!.bounds);web=view;view.uiDelegate=self;view.autoresizingMask=[.width,.height];window.contentView=view;view.load(URLRequest(url:URL(string:"http://127.0.0.1:18680/manage")!));window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
 }
 func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{return true}
}
let app=NSApplication.shared;let delegate=Delegate();app.delegate=delegate;app.setActivationPolicy(.regular);app.run()
