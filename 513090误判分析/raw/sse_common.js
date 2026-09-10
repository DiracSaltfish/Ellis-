//下拉框拼接数据内容
function addSelectOption(option) {
  var optionHtml = "";
  $.each(option, function (k, v) {
    if (v.children) {
      optionHtml += '<optgroup value="' + v.value + '" label = "'+ v.name +'">';
      $.each(v.children, function (i, j) {
        optionHtml += '<option value="' + j.value + '">' + j.name + '</option>';
      })
      optionHtml += '</optgroup>';
    } else {
      optionHtml += '<option value="' + v.value + '">' + v.name + "</option>";
    }
  });
  return optionHtml;
}

//左侧查询条件start
var common_search_module = {
	init: function(data) {
		if (!data) {
			return;
		}
		this.initQueryDom(data)
	},
	initQueryDom: function(data) {
		var queryParams = data.queryParams;
		if (!queryParams || queryParams.length == 0) {
			return;
		}
		var _this = this;
		var docHtml = [];
		queryParams.forEach(function(item) {
			// console.log(_this.generateQueryItem(item));
			docHtml = docHtml.concat(_this.generateQueryItem(item));
		})
		if (data.type == 'announcement') {
			$('.left_menuOption ').after('<div class="search_inputCol">' + docHtml.join('') + '</div>');
                        if(data.noMenu){
                            $('.left_menuOption ').remove(); 
                        }
		} else if (data.type == 'listing') {
      if(window.matchMedia("(max-width: 1199px)").matches) {
        $('.js_listingMobile').prepend(docHtml.join(''))
      } else {
        $('.listing-form').prepend(docHtml.join(''))
      }
    } else {
			$('.left_menuOption .left_sideMenu').after(docHtml.join(''));
		}
	},
	generateQueryItem: function(item) {
		var _this = this;
		var docHtml = [];
		if (item['type'] == '10') {
			//证券代码或简称
			var zqTitle = item['title'] ? item['title'] : '证券代码或简称'
			var zqPlaceholder = item['placeholder'] ? item['placeholder'] :
				item['title'] == "证券代码或扩位简称" ? "6位代码 / 扩位简称" : '6位代码 / 简称'
			docHtml.push('<div class="sse_outerItem"><div class="sse_typeTitle">' + zqTitle + '</div>');
			docHtml.push('<div class="sse_searchInput">');
			//处理证券code在接口参数不一致的情况
			var zqName = "stockcode";
			if (item["name"]) {
				zqName = item["name"];
			}
			var zqClass = "js_code00";
			if(item.class){
				zqClass = item.class
			}
			docHtml.push('<input id="inputCode" name="' + zqName +
				'" autocomplete="off" class="form-control sse_input sse_input_req ' + zqClass +
				'" type="text" placeholder="' + zqPlaceholder + '">'
			);
			docHtml.push('<span class="search_btn bi-search"></span>');
                        if(item.noTips===true){
                           docHtml.push('</div></div>');
                        }else{
                           docHtml.push('<div class="bdsug" id="js_autocomplete" style=" width: auto; display: none;">');
			   docHtml.push('<ul class="search-history" id="js_panMenu_history"></ul><ul id="js_panMenu"></ul></div>');
                           docHtml.push('</div></div>');
                        }
			
			
		} else if (item['type'] == '11') {
			//板块
			var bkTitle = item['title'] ? item['title'] : '板块';
			var bkClass = item['class'] ? item['class'] : 'js_plate';
			docHtml.push('<div class="sse_outerItem ' + bkClass + '"><div class="sse_typeTitle">' + bkTitle +
				'</div><div class="sse_searchInput">');
			var bkName = "type";
			if (item["name"]) {
				bkName = item["name"];
			}
			docHtml.push('<select class="selectpicker sse_selectpicker_req" name="' + bkName +
				'" data-size="8">');
			docHtml.push(addSelectOption(_this.plateOption));
			docHtml.push('</select></div></div>');
		} else if (item['type'] == '12') {
			var sTime = "",
				eTime = "",
				tName;
			if (item["start"]) {
				sTime = item["start"];
			}
			if (item["end"]) {
				eTime = item["end"];
			}
			if (item["name"]) {
				tName = item["name"];
			}
			if (item["range"] == 'false') {
				_this.range = false;
			}
			var tt = '日期范围';
			if (item['class'] == 'js_date') {
				tt = '日期'
			}
			var rqsTitle = item['title'] ? item['title'] : item['title'] == '' ? '' : tt
			if (item["isHour"] == 'true') {
				_this.isHour = true;
			}
			if (item["btns"] && item["btns"].length > 0) {
				_this.btns = item["btns"];
			}
			docHtml.push('<div class="sse_outerItem ' + item['class'] + '"><div class="sse_typeTitle">' +
				rqsTitle + '</div><div class="sse_searchInput">');
			docHtml.push('<input class="form-control sse_input" type="text" name="' + tName + '" start="' +
				sTime + '" end="' + eTime + '" placeholder="开始时间  至  结束时间" readonly>');
			docHtml.push('<span class="bi-calendar4-week"></span></div></div>');
		}
    else if (item['type'] == '14') {
			//多选下拉框
			docHtml.push('<div class="sse_outerItem ' + item['class'] + '">');
			docHtml.push('<div class="sse_typeTitle">' + item['title'] + '</div><div class="sse_searchInput">');
			docHtml.push('<select class="multiple-select" multiple="multiple" style="width: 100%; display: none;">');
			docHtml.push(addSelectOption(item['options']));
			docHtml.push('</select></div></div>');
		}
		else if (item['type'] == '1') {
			//输入框
			var zqClass1 = item.class1 || 'js_code3'
			var zqPlaceholder = item.placeholder ? item.placeholder : item.title;
			docHtml.push('<div class="sse_outerItem ' + item['class'] + '">');
			docHtml.push('<div class="sse_typeTitle">' + item['title'] + '</div>');
			docHtml.push('<div class="sse_searchInput">');
			docHtml.push('<input id="' + item['id'] + '" autocomplete="off" name="' + item['name'] +
				'" class="form-control sse_input ' + zqClass1 + '" type="text" placeholder="' +
				zqPlaceholder + '">');
                        var btnClass = item.btnClass || "bi-search";
			docHtml.push('<span class="search_btn '+ btnClass + '"></span>');
                        if(item.noTips===true){
                           docHtml.push('</div></div>');
                        }else{
                           docHtml.push('<div class="bdsug" id="js_autocomplete" style=" width: auto; display: none;">');
			   docHtml.push('<ul class="search-history" id="js_panMenu_history"></ul><ul id="js_panMenu"></ul></div>');
                           docHtml.push('</div></div>');
                        }
			
		} else if (item['type'] == '2') {
			//下拉框
			docHtml.push('<div class="sse_outerItem ' + item['class'] + '">');
			docHtml.push('<div class="sse_typeTitle">' + item['title'] + '</div><div class="sse_searchInput">');
			docHtml.push('<select class="selectpicker sse_selectpicker_req" name="' + item['name'] +
				'" data-size="8">');
			docHtml.push(addSelectOption(item['options']));
			docHtml.push('</select></div></div>');
		} else if (item['type'] == '4') {
			//单选框
			docHtml.push('<div class="sse_outerItem ' + item['class'] + '">');
			docHtml.push('<div class="sse_typeTitle">' + item['title'] + '</div>');
			docHtml.push('<div class="sse_searchInput">');
			docHtml.push('</div></div>');
		} else if (item['type'] == '5') {
			//公告类筛选框
                        if(item['hasWrap'] && item['wrapTitle']){
                           var wrapClass = item['wrapClass']? item['wrapClass'] : '';
                           var wrapTitle = item['wrapTitle'];
                           
                           docHtml.push('<div class="sse_outerItem ' + wrapClass + '"><div class="sse_typeTitle">' + wrapTitle + '</div>');
                        }
			docHtml.push('<div class="' + item['class'] + '">');
			docHtml.push('<div class="' + item['class1'] + '"></div>');
			if (item['hasOpen']) {
				docHtml.push(
				'<div class="announceShow">展开全部<span class="bi-chevron-double-down"></span></div>');
			}
			docHtml.push('</div>');
                        
                        if(item['hasWrap'] && item['wrapTitle']){
                           docHtml.push('</div>');  
                        }
		}
		return docHtml;
	},
}
try {
	if (queryConfigData) { //左侧条件和表格初始化
		common_search_module.init(queryConfigData[channelCode]);
	}
} catch (e) {}
//左侧查询条件end
