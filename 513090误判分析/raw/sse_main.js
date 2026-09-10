/**
 * 仅用于注册添加公共js插件、组件
 * 调用请在sse_utils_2021内添加
 * 页面单独js通过.js_require_on_page按先后顺序添加
 */
//插件类
var jq_path = ["../lib/jquery.min"];
var fastclick_path = ["../lib/fastclick.min"]; //移动端快速点击事件
var dlMenu_path = ["../lib/jquery.ssedlmenu"]; //手机端菜单插件
var modernizr_path = ["../lib/modernizr.custom"]; //浏览器兼容库 移动端菜单需求
var conversion_path = ["../lib/sse-chinese-conversion"]; //中文简繁切换
var langSwitch_path_cn = ["../lib/sse-lang-switch-cn"]; //中英切换
var bootstrap_path = ["../lib/2021/bootstrap.bundle-v4.5.0"];
var laydate_path = ["../lib/2021/laydate-v5.3.1"]; //laydate日期选择
var select_path = ["../lib/2021/bootstrap-select-v1.13.9"]; //bootstrap下拉框
var swiper_path = ["../lib/2021/swiper.min-v5.4.5"];
var highstock_path = ["../lib/2021/highstock-v6.0.4"]; //highstock图表
var highmore_path = ["../lib/2021/highcharts-more-v6.0.4"]; //highstock图表
var highsolidgauge_path = ["../lib/2021/solid-gauge-v6.0.4"]; //highstock图表
var drillDown_path = ["../lib/2021/drillDown-v6.0.4"]; //交易信息披露柱状图
var echarts_path = ["../lib/2021/echarts.min-v5.0.2"]; //echarts图——规则总览
var wordcloud_path = ["../lib/2021/wordcloud-v6.0.4"]; //词云图
var wow_path = ["../lib/2021/wow-v1.1.3"]; //页面滚动动画
var qrcode_path = ["../lib/2021/qrcode.min"]; //二维码生成
var jcarousel_path = ["../lib/2021/jcarousel.min"]; //新蓝筹内容页轮播
var markup_path=["../common/page_list_category"];
var markuptt_path=["../common/page_list_render"];
var sse_introduction_path = ["./sse_introduction"]
// highcharts插件

//方法类
var menuEvents_path = ["./menuEvents_2021"]; //公共菜单事件
var popularize_path = ["./popularize_2021"];
var sse_utils_path = ["./sse_utils_2021"]; //判断js引入方法
var searchCommon_path = [
  "/xhtml/home/2021public/querySearch/search_common_2021",
]; //网站公共方法

var others_path = ["/xhtml/js/others"];

var index = 0;
require.config({
  paths: {
    jquery: jq_path[index],
    fastclick: fastclick_path[index],
    bootstrap: bootstrap_path[index],
    bootstrapSelect: select_path[index],
    laydate: laydate_path[index],
    swiper: swiper_path[index],
    modernizr: modernizr_path[index],
    dlMenu: dlMenu_path[index],
    menuEvents: menuEvents_path[index],
    popularize: popularize_path[index],
    sse_utils: sse_utils_path[index],
    searchCommon: searchCommon_path[index],
    highstock: highstock_path[index],
    highMore: highmore_path[index],
    solidGauge: highsolidgauge_path[index],
    drillDown: drillDown_path[index],
    echarts: echarts_path[index],
    wordcloud: wordcloud_path[index],
    wow: wow_path[index],
    qrcode: qrcode_path[index],
    jcarousel: jcarousel_path[index],
    markup: markup_path[index],
    markuptt: markuptt_path[index],
    others: others_path[index],
    conversion: conversion_path[index],
    langSwitch: langSwitch_path_cn[index],
    sse_introduction: sse_introduction_path[index],
  },
  shim: {
    bootstrapSelect: {
      deps: ["bootstrap"],
    },
    dlMenu: {
      deps: ["modernizr"],
    },
    markup: {
      deps: ["jquery"],
    },
    markuptt: {
      deps: ["jquery"],
    },
    others: {
      deps: ["jquery"],
    },
    langSwitch: {
      deps: ["jquery"],
    },
  },
});

//移动端快速点击事件
require(["fastclick"], function (fastclick) {
  if (window.FastClick) {
    // 初始化fastclick
    window.FastClick.attach(document.body);
  }
});
//引入公共方法
require(["jquery"], function ($) {
  //公共菜单
  require(["menuEvents"]);
  require(["others"]);
  require(["bootstrap", "popularize", "sse_utils"], function () {
    require(["searchCommon"], function () {
      //页面单独加载js文件方法
      if ($(".js_require_on_page").length > 0) {
        $jsFileDefines = $(".js_require_on_page");
        $jsFileDefines.each(function (index) {
          var $getAtt = $(this).attr("js_files");
          if ($getAtt.length > 0) {
            var jsFiles = $getAtt.split(",");
            if (jsFiles.length > 0) {
              setFilesArr(jsFiles);
            }
          }
        });
      }
    });
    require(["conversion"], function () {
      var locationStr = window.location.href;
      if(locationStr.indexOf("big5.sse.com.cn") >= 0){
        zh_tran('t');
      }
    });
    require(["sse_introduction"], function (e) {
      e.init();
    })
    // <!-- WeChat share -->
    if (/micromessenger/.test(navigator.userAgent.toLowerCase())) {
      require(['/xhtml/js/app/sse_wechatassistant.js'], function (sseWechatAssistant) {
          sseWechatAssistant.init('wx8fd294d57db35754');
      });
    }
  });
  require(["langSwitch"], function () {
    switch_init();
  });
});

//加载js文件
function setFilesArr(jsFiles) {
  var item = [];
  item.push(jsFiles[0]);
  require(item, function () {
    jsFiles.splice(0, 1);
    if (jsFiles.length > 0) {
      setFilesArr(jsFiles);
    }
  });
}
