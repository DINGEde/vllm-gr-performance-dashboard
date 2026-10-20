/* Left-hand page outline.
 *
 * The dashboard pages are long, and their sections are hand-written HTML
 * instead of Markdown headings, so MkDocs' built-in table of contents only
 * sees the page title. This script builds the outline from the rendered
 * document instead, which keeps working when the builders regenerate the
 * Markdown from runs/.
 */
(function () {
  "use strict";

  var MIN_SECTIONS = 2;
  var LABEL = "On this page";
  var ACTIVE_OFFSET = 96;

  function mainRegion() {
    return (
      document.querySelector(".col-md-9[role='main']") ||
      document.querySelector("main[role='main']") ||
      document.querySelector("main")
    );
  }

  function slugBase(text, index) {
    var slug = String(text)
      .toLowerCase()
      .replace(/&/g, " and ")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return slug || "section-" + (index + 1);
  }

  function isVisible(element) {
    return element.offsetParent !== null;
  }

  // MkDocs appends a "¶" permalink anchor to Markdown headings; it is hidden
  // by CSS but would still leak into the outline label.
  function headingLabel(heading) {
    var clone = heading.cloneNode(true);
    Array.prototype.slice
      .call(clone.querySelectorAll("a.headerlink"))
      .forEach(function (anchor) {
        anchor.parentNode.removeChild(anchor);
      });
    return clone.textContent.trim();
  }

  function collectSections(main) {
    var used = {};
    return Array.prototype.slice
      .call(main.querySelectorAll("h2"))
      .filter(function (heading) {
        if (!headingLabel(heading)) return false;
        // Run history and the latest-run hero render data-dependent <h2>
        // elements; they are card titles, not page sections. Both the GPU and
        // the NPU page use the same classes and id suffixes.
        if (heading.closest(".vgr-latest, [id$='-run-history']")) return false;
        return isVisible(heading);
      })
      .map(function (heading, index) {
        var label = headingLabel(heading);
        var id = heading.id;
        if (!id) {
          var base = slugBase(label, index);
          id = base;
          var suffix = 2;
          while (document.getElementById(id) || used[id]) {
            id = base + "-" + suffix;
            suffix += 1;
          }
          heading.id = id;
        }
        used[id] = true;
        return { heading: heading, id: id, label: label };
      });
  }

  function buildNav(sections) {
    var nav = document.createElement("nav");
    nav.className = "page-outline";
    nav.setAttribute("aria-label", LABEL);

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "page-outline-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = LABEL;

    var list = document.createElement("ul");
    list.className = "page-outline-list";

    sections.forEach(function (section) {
      var item = document.createElement("li");
      var link = document.createElement("a");
      link.className = "page-outline-link";
      link.href = "#" + section.id;
      link.textContent = section.label;
      item.appendChild(link);
      list.appendChild(item);
      section.link = link;
    });

    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    list.addEventListener("click", function (event) {
      if (!event.target.closest("a")) return;
      nav.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    });

    nav.appendChild(toggle);
    nav.appendChild(list);
    return nav;
  }

  function trackActiveSection(sections) {
    if (!sections.length) return;
    var pending = false;

    function update() {
      pending = false;
      var active = sections[0];
      sections.forEach(function (section) {
        if (section.heading.getBoundingClientRect().top - ACTIVE_OFFSET <= 0) {
          active = section;
        }
      });
      var atBottom =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 2;
      if (atBottom) active = sections[sections.length - 1];

      sections.forEach(function (section) {
        var isActive = section === active;
        section.link.classList.toggle("is-active", isActive);
        if (isActive) {
          section.link.setAttribute("aria-current", "true");
        } else {
          section.link.removeAttribute("aria-current");
        }
      });
    }

    function schedule() {
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(update);
    }

    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // The dashboard grows after its data fetch resolves, so a hash landing can
    // be scrolled before the target settles. Re-apply on load and hashchange.
    window.addEventListener("load", schedule);
    window.addEventListener("hashchange", schedule);
    update();
  }

  function init() {
    var main = mainRegion();
    if (!main) return;

    var sections = collectSections(main);
    if (sections.length < MIN_SECTIONS) return;

    var nav = buildNav(sections);
    var row = main.parentElement;
    if (row && row.classList.contains("row")) {
      row.classList.add("has-page-outline");
      row.insertBefore(nav, row.firstElementChild);
    } else {
      main.parentElement.insertBefore(nav, main);
    }

    trackActiveSection(sections);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
