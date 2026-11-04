(function (global) {
  'use strict';

  var instance = null;
  var defaults = {
    hmUrl: '/hm.gif',
    logUrl: '/log.gif',
    autoTrackPage: true,
    autoTrackUnload: true,
    autoTrackClick: true,
    debug: false,
    root: null,
    extraBaseParams: null
  };

  function assign(target) {
    for (var i = 1; i < arguments.length; i++) {
      var source = arguments[i] || {};
      for (var key in source) {
        if (Object.prototype.hasOwnProperty.call(source, key)) {
          target[key] = source[key];
        }
      }
    }
    return target;
  }

  function getDocument() {
    return global.document;
  }

  function getNavigator() {
    return global.navigator || {};
  }

  function sendByImage(url, prefix) {
    if (!url) {
      return;
    }
    var ImageCtor = global.Image;
    if (!ImageCtor) {
      return;
    }
    var img = new ImageCtor();
    var key = (prefix || 'project_log_') + Math.floor(Math.random() * 2147483648).toString(36);
    global[key] = img;
    img.onload = img.onerror = img.onabort = function () {
      img.onload = img.onerror = img.onabort = null;
      global[key] = null;
      img = null;
    };
    img.src = url;
  }

  function encodeQuery(params) {
    var query = [];
    for (var key in params) {
      if (Object.prototype.hasOwnProperty.call(params, key) && params[key] !== '' && params[key] != null) {
        query.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(params[key])));
      }
    }
    return query.length ? '?' + query.join('&') : '';
  }

  function getCookie(name) {
    var doc = getDocument();
    if (!doc || !doc.cookie) {
      return null;
    }
    var reg = new RegExp('(?:^| )' + name + '=([^;]*)(?:;|$)');
    var match = doc.cookie.match(reg);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function setCookie(name, value, day) {
    var doc = getDocument();
    if (!doc) {
      return;
    }
    var cookie = name + '=' + encodeURIComponent(value || '') + ';domain=.nstl.gov.cn;path=/';
    if (typeof day === 'number' && day !== 0) {
      var expires = day * 24 * 60 * 60 * 1000;
      var date = new Date(+new Date() + expires);
      cookie += ';expires=' + date.toUTCString();
    }
    doc.cookie = cookie;
  }

  function getUuid() {
    var cryptoObj = global.crypto;
    if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
      return cryptoObj.randomUUID();
    }
    if (cryptoObj && typeof cryptoObj.getRandomValues === 'function' && typeof global.Uint8Array === 'function') {
      return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, function (c) {
        var num = Number(c);
        return (num ^ (cryptoObj.getRandomValues(new global.Uint8Array(1))[0] & (15 >> (num / 4)))).toString(16);
      });
    }
    var timestamp = new Date().getTime();
    var perfNow = global.performance && global.performance.now ? global.performance.now() * 1000 : 0;
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var random = Math.random() * 16;
      if (timestamp > 0) {
        random = (timestamp + random) % 16 | 0;
        timestamp = Math.floor(timestamp / 16);
      } else {
        random = (perfNow + random) % 16 | 0;
        perfNow = Math.floor(perfNow / 16);
      }
      return (c === 'x' ? random : (random & 0x3) | 0x8).toString(16);
    });
  }

  function collectPerformance() {
    var performanceAlias = global.performance || global.mozPerformance || global.msPerformance || global.webkitPerformance;
    if (!performanceAlias) {
      return {};
    }
    var performanceData = typeof performanceAlias.getEntriesByType === 'function' && performanceAlias.getEntriesByType('navigation')
      ? performanceAlias.getEntriesByType('navigation')[0]
      : performanceAlias.timing;
    var metrics = {};
    if (!performanceData) {
      return metrics;
    }
    function add(name, end, start) {
      if (performanceData[end] && performanceData[start] && performanceData[end] >= performanceData[start]) {
        metrics[name] = performanceData[end] - performanceData[start];
      }
    }
    add('tcpt', 'connectEnd', 'fetchStart');
    add('rpt', 'responseStart', 'requestStart');
    add('rqt', 'responseEnd', 'responseStart');
    add('dt', 'domInteractive', 'domLoading');
    add('dat', 'domComplete', 'domInteractive');
    add('let', 'loadEventEnd', 'loadEventStart');
    return metrics;
  }

  function collectBaseLog(options) {
    var doc = getDocument();
    var nav = getNavigator();
    var screen = global.screen || {};
    var now = new Date();
    var referrer = doc ? doc.referrer || '' : '';
    var location = doc && doc.location ? doc.location : global.location || {};
    var list = {
      im: options.hmUrl,
      ck: nav.cookieEnabled ? '1' : '0',
      ja: typeof nav.javaEnabled === 'function' && nav.javaEnabled() ? '1' : '0',
      cl: (screen.colorDepth || 0) + '-bit',
      ds: (screen.width || 0) + 'x' + (screen.height || 0),
      ln: String(nav.language || nav.browserLanguage || nav.systemLanguage || nav.userLanguage || '').toLowerCase(),
      su: referrer,
      tt: doc ? doc.title || '' : '',
      u: location.href || '',
      ph: location.pathname || '',
      lt: getUuid(),
      ch: doc ? doc.charset || '' : '',
      rnd: Math.floor((Math.random() + Math.floor(Math.random() * 9 + 1)) * Math.pow(10, 9)),
      nrs: '0',
      h: now.getHours(),
      m: now.getMinutes(),
      s: now.getSeconds()
    };

    var mimeTypeMap = {
      pdf: 'application/pdf',
      qt: 'video/quicktime',
      realp: 'audio/x-pn-realaudio-plugin',
      wma: 'application/x-mplayer2',
      fla: 'application/x-shockwave-flash',
      java: 'application/x-java-vm',
      ag: 'application/x-silverlight'
    };
    for (var key in mimeTypeMap) {
      if (Object.prototype.hasOwnProperty.call(mimeTypeMap, key)) {
        var mimeType = nav.mimeTypes && nav.mimeTypes[mimeTypeMap[key]];
        list[key] = mimeType && mimeType.enabledPlugin ? '1' : '0';
      }
    }

    setCookie('nstl_rsp', referrer, 0);
    if (referrer.indexOf('xueshu.baidu') > -1 || getCookie('nstl_rs') === '1' || getCookie('t') === 'b') {
      list.nrs = '1';
    }

    return assign({}, list, collectPerformance(), options.extraBaseParams || {});
  }

  function parseLogValue(node) {
    if (!node || !node.getAttribute) {
      return null;
    }
    var value = node.getAttribute('data-log');
    if (!value) {
      return null;
    }
    if (typeof value === 'object') {
      return value;
    }
    try {
      return JSON.parse(String(value).replace(/&quot;/g, '"').replace(/&apos;/g, "'"));
    } catch (error) {
      return {};
    }
  }

  function findAttrNode(node, attrName) {
    for (var current = node; current && current.nodeType === 1; current = current.parentNode) {
      if (current.getAttribute && current.getAttribute(attrName)) {
        return current;
      }
      if (current.nodeName === 'BODY') {
        break;
      }
    }
    return null;
  }

  function findLogNode(node) {
    for (var current = node; current && current.nodeType === 1; current = current.parentNode) {
      if ((current.tagName || '').toLowerCase() === 'a' || parseLogValue(current)) {
        if (parseLogValue(current)) {
          return current;
        }
      }
      if (current.nodeName === 'BODY') {
        break;
      }
    }
    return null;
  }

  function closestByClass(node, className) {
    for (var current = node; current && current.nodeType === 1; current = current.parentNode) {
      var cls = current.getAttribute && current.getAttribute('class');
      if (cls && (' ' + cls + ' ').indexOf(' ' + className + ' ') > -1) {
        return current;
      }
    }
    return null;
  }

  function childrenByClass(parent, className) {
    var result = [];
    if (!parent || !parent.childNodes) {
      return result;
    }
    for (var i = 0; i < parent.childNodes.length; i++) {
      var child = parent.childNodes[i];
      var cls = child.getAttribute && child.getAttribute('class');
      if (cls && (' ' + cls + ' ').indexOf(' ' + className + ' ') > -1) {
        result.push(child);
      }
    }
    return result;
  }

  function enrichListItemLog(log, logNode) {
    var item = closestByClass(logNode, 'result-list-con');
    if (!item) {
      return log;
    }
    if (!log.num || log.type === 'list-item-button') {
      var title = findDescendantByClass(item, 'result-list-tit');
      var dataNum = title && title.getAttribute && title.getAttribute('data-num');
      if (dataNum) {
        log.num = Number(dataNum);
      } else if (item.parentNode) {
        var siblings = childrenByClass(item.parentNode, 'result-list-con');
        for (var i = 0; i < siblings.length; i++) {
          if (siblings[i] === item) {
            log.num = i + 1;
            break;
          }
        }
      }
    }
    if (!log.id || log.type === 'list-item-button') {
      var titleNode = findDescendantByClass(item, 'result-list-tit');
      var id = titleNode && titleNode.getAttribute && titleNode.getAttribute('data-id');
      if (id) {
        log.id = id;
      }
    }
    return log;
  }

  function findDescendantByClass(node, className) {
    if (!node || !node.childNodes) {
      return null;
    }
    for (var i = 0; i < node.childNodes.length; i++) {
      var child = node.childNodes[i];
      var cls = child.getAttribute && child.getAttribute('class');
      if (cls && (' ' + cls + ' ').indexOf(' ' + className + ' ') > -1) {
        return child;
      }
      var found = findDescendantByClass(child, className);
      if (found) {
        return found;
      }
    }
    return null;
  }

  var tabNames = {
    'paperDetailMatrix-preamble': '前言',
    'paperDetailMatrix-hasReview': '书评',
    'paperDetailMatrix-catalog': '目录',
    'paperDetailMatrix-recommend': '文章',
    'paperDetailMatrix-smjournals': '相关推荐',
    'paperDetailMatrix-analysis': '统计分析',
    'paperDetailMatrix-relationship': '关系发现',
    'paperDetailMatrix-appraisal': '评价',
    'paperDetail-original': '原文',
    'paperDetail-recommend': '相关推荐',
    'paperDetail-reference': '参考文献',
    'paperDetail-interrelated': '相关数据',
    'paperDetail-network': '引文网络',
    'paperDetail-analysis': '统计分析',
    'paperDetail-relationship': '关系发现',
    'paperDetail-appraisal': '评价',
    'author-literature': '作者文献',
    'author-article': '文章',
    'author-network': '作者网络',
    'author-relationship': '关系网络',
    'author-recommend': '学者推荐'
  };

  function NstlTracker(options) {
    this.options = assign({}, defaults, options || {});
    this.baseLog = collectBaseLog(this.options);
    this.startTime = new Date().getTime();
    this.started = false;
    this.unloaded = false;
    this.boundClick = this.handleClick.bind(this);
    this.boundUnload = this.handleBeforeUnload.bind(this);
  }

  NstlTracker.prototype.start = function () {
    if (this.started) {
      return this;
    }
    this.started = true;
    global.hm_basic_log = this.baseLog;
    global.sendOtherLog = this.trackOther.bind(this);
    if (this.options.autoTrackClick) {
      this.bindClick();
    }
    if (this.options.autoTrackUnload) {
      this.bindUnload();
    }
    if (this.options.autoTrackPage) {
      this.trackPage();
    }
    return this;
  };

  NstlTracker.prototype.bindClick = function () {
    var doc = getDocument();
    var root = this.options.root || doc;
    if (!root) {
      return;
    }
    if (root.addEventListener) {
      root.addEventListener('click', this.boundClick, false);
    } else {
      root.onclick = this.boundClick;
    }
  };

  NstlTracker.prototype.bindUnload = function () {
    if (global.addEventListener) {
      global.addEventListener('beforeunload', this.boundUnload, false);
    } else {
      global.onbeforeunload = this.boundUnload;
    }
  };

  NstlTracker.prototype.trackPage = function (data) {
    var payload = assign({}, this.baseLog, data || {});
    payload.im = this.options.hmUrl;
    this.baseLog = assign(this.baseLog, payload);
    global.hm_basic_log = this.baseLog;
    var url = this.options.hmUrl + encodeQuery(payload);
    this.debug(url, payload);
    sendByImage(url, 'project_hm_log_');
  };

  NstlTracker.prototype.handleBeforeUnload = function () {
    if (this.unloaded) {
      return;
    }
    this.unloaded = true;
    var unloadData = {
      et: new Date().getTime()
    };
    unloadData.ep = unloadData.et - this.startTime;
    this.trackPage(unloadData);
  };

  NstlTracker.prototype.handleClick = function (event) {
    event = event || global.event;
    var target = event && (event.target || event.srcElement);
    var clastagNode = findAttrNode(target, 'clastag');
    if (!clastagNode) {
      return;
    }
    var logNode = findLogNode(target);
    if (!logNode) {
      return;
    }
    var log = assign({}, parseLogValue(logNode) || {});
    var clastag = clastagNode.getAttribute('clastag');
    if (tabNames[clastag]) {
      log.tabsName = tabNames[clastag];
    } else if (clastag === 'searchResultItemBody') {
      log = enrichListItemLog(log, logNode);
    } else if (clastag === 'searchResultTree') {
      log = {
        type: 'tree',
        text: logNode.getAttribute('title') || logNode.textContent || ''
      };
    }
    var doc = getDocument();
    log.path = doc && doc.location ? doc.location.pathname : '';
    this.track(log);
  };

  NstlTracker.prototype.track = function (data) {
    var payload = assign({}, this.baseLog, data || {});
    payload.im = this.options.logUrl;
    var url = this.options.logUrl + '?data=' + encodeURIComponent(JSON.stringify(payload));
    this.debug(url, payload);
    sendByImage(url, 'project_log_');
  };

  NstlTracker.prototype.trackOther = function (data) {
    data = data || {};
    if (data.pageNumber) {
      data = {
        type: 'pagination',
        pageNumber: data.pageNumber,
        pageSize: data.pageSize,
        totalPage: data.totalPage,
        page: getDocument() && getDocument().location ? getDocument().location.pathname : ''
      };
    }
    this.track(data);
  };

  NstlTracker.prototype.debug = function (url, payload) {
    if (this.options.debug && global.console && global.console.log) {
      global.console.log('[NstlTracker]', url, payload);
    }
  };

  NstlTracker.init = function (options) {
    if (instance) {
      if (options) {
        instance.options = assign(instance.options, options);
      }
      return instance.start();
    }
    instance = new NstlTracker(options);
    return instance.start();
  };

  NstlTracker.getInstance = function () {
    return instance;
  };

  NstlTracker.create = function (options) {
    return new NstlTracker(options);
  };

  global.NstlTracker = NstlTracker;
  if (typeof module === 'object' && module.exports) {
    module.exports = NstlTracker;
  }
})(typeof window !== 'undefined' ? window : globalThis);
