#include "IopvWebView.h"
#include <QWindow>
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

@interface IopvWebDelegate : NSObject <WKUIDelegate, WKNavigationDelegate>
@property(nonatomic,strong) NSURL *targetURL;
@end
@implementation IopvWebDelegate
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
 __weak WKWebView *weakView=webView;NSURL *url=self.targetURL;
 dispatch_after(dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC),dispatch_get_main_queue(),^{WKWebView *v=weakView;if(v&&v.navigationDelegate)[v loadRequest:[NSURLRequest requestWithURL:url]];});
}
- (void)webView:(WKWebView *)webView runJavaScriptTextInputPanelWithPrompt:(NSString *)prompt defaultText:(NSString *)text initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSString *))handler {
 NSAlert *a=[[NSAlert alloc] init];a.messageText=prompt;[a addButtonWithTitle:@"保存"];[a addButtonWithTitle:@"取消"];
 NSTextField *field=[[NSTextField alloc] initWithFrame:NSMakeRect(0,0,420,24)];field.stringValue=text?:@"";a.accessoryView=field;
 [a beginSheetModalForWindow:webView.window completionHandler:^(NSModalResponse response){handler(response==NSAlertFirstButtonReturn?field.stringValue:nil);}];
}
@end
class IopvWebWidget : public QWidget {
 WKWebView *view_; IopvWebDelegate *delegate_;
public:
 IopvWebWidget(const QUrl &url,QWidget *parent):QWidget(parent){
 setAttribute(Qt::WA_NativeWindow);NSView *host=(__bridge NSView *)(void *)winId();
 view_=[[WKWebView alloc] initWithFrame:host.bounds];delegate_=[IopvWebDelegate new];view_.UIDelegate=delegate_;view_.navigationDelegate=delegate_;delegate_.targetURL=[NSURL URLWithString:[NSString stringWithUTF8String:url.toEncoded().constData()]];view_.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
 [host addSubview:view_];[view_ loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithUTF8String:url.toEncoded().constData()]]]];
 }
 ~IopvWebWidget(){[view_ stopLoading];view_.UIDelegate=nil;view_.navigationDelegate=nil;[view_ removeFromSuperview];}
};
QWidget *createIopvWebView(const QUrl &url,QWidget *parent){return new IopvWebWidget(url,parent);}
