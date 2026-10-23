(function () {
  var ua = navigator.userAgent || "";
  var platform = /Windows/i.test(ua) ? "windows" : (/Linux|X11/i.test(ua) ? "linux" : "");
  if (!platform) return;
  var links = document.querySelectorAll(".os-download");
  for (var i = 0; i < links.length; i += 1) {
    var link = links[i];
    var href = link.getAttribute("data-" + platform + "-href");
    var label = link.getAttribute("data-" + platform + "-label");
    if (href) link.setAttribute("href", href);
    if (label) link.textContent = label;
  }
})();
