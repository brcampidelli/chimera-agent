import { describe, expect, it } from "vitest";

import fixture from "@/lib/design/__fixtures__/html-element-index.json";
import { annotateForEditing } from "@/lib/design/htmlArtboard";
import { describeElement, HtmlEditRefused, locateElement, scanTags, setStyleInSource, setTextInSource } from "@/lib/design/htmlElements";

const cases = (fixture as unknown as { cases: { source: string; tags: [number, string][] }[] }).cases;

function startTagsOf(source: string): [number, string][] {
  return scanTags(source)
    .filter((token) => token.kind === "start")
    .map((token, index) => [index, token.tag] as [number, string]);
}

describe("element ordinals match the Python locator", () => {
  it.each(cases.map((item, number) => [number, item.source, item.tags] as const))(
    "case %i numbers the same start tags in the same order",
    (_number, source, tags) => {
      expect(startTagsOf(source)).toEqual(tags);
    },
  );

  it.each(cases.map((item, number) => [number, item.source, item.tags] as const))(
    "case %i stamps those ordinals onto the same elements in the editor",
    (_number, source, tags) => {
      const stamped: [number, string][] = [];
      const doc = new DOMParser().parseFromString(annotateForEditing(source), "text/html");
      for (const element of Array.from(doc.querySelectorAll("[data-chimera-i]"))) {
        stamped.push([Number(element.getAttribute("data-chimera-i")), element.localName]);
      }
      const expected = tags.filter(([, tag]) => !["tbody"].includes(tag));
      expect(stamped).toEqual(expected);
    },
  );
});

describe("annotateForEditing", () => {
  it("stamps after the tag name and keeps every other byte", () => {
    expect(annotateForEditing('<main><p class="a">x</p><br/></main>')).toBe(
      '<main data-chimera-i="0"><p data-chimera-i="1" class="a">x</p><br data-chimera-i="2"/></main>',
    );
  });

  it("does not number comments, raw text or close tags", () => {
    expect(annotateForEditing("<!-- <div> --><textarea><b></textarea><i>y</i>")).toBe(
      '<!-- <div> --><textarea data-chimera-i="0"><b></textarea><i data-chimera-i="1">y</i>',
    );
  });
});

describe("locateElement and describeElement", () => {
  const source = '<main><p style="color: red; padding: 2px">Hello &amp; bye</p><svg><path/></svg></main>';

  it("reports the text of a text-only element and its inline style", () => {
    expect(describeElement(source, 1)).toEqual({ tag: "p", inlineStyle: "color: red; padding: 2px", text: "Hello & bye" });
    expect(describeElement(source, 0)?.text).toBeNull();
    expect(describeElement(source, 3)).toEqual({ tag: "path", inlineStyle: "", text: null });
    expect(describeElement(source, 99)).toBeNull();
  });

  it("reports unclosed and self-closing elements as such", () => {
    expect(locateElement("<main><p>a</main>", 1)).toMatchObject({ closed: false, textOnly: true });
    expect(locateElement("<svg><path/></svg>", 1)).toMatchObject({ selfClosing: true, closed: false });
  });
});

describe("setStyleInSource mirrors the Python edit", () => {
  it("replaces, appends and removes one declaration, in the same bytes", () => {
    expect(setStyleInSource('<main><p style="color: red; padding: 2px">Hello</p></main>', 1, "color", "blue")).toBe(
      '<main><p style="padding: 2px; color: blue">Hello</p></main>',
    );
    expect(setStyleInSource('<main><p style="padding: 2px; color: blue">Hello</p></main>', 1, "color", "")).toBe(
      '<main><p style="padding: 2px">Hello</p></main>',
    );
  });

  it("drops the attribute when no declaration is left", () => {
    expect(setStyleInSource('<main><p class="a" style="color: red">x</p></main>', 1, "color", "")).toBe('<main><p class="a">x</p></main>');
  });

  it("does not split a semicolon inside a quoted string", () => {
    expect(setStyleInSource("<main><p style=\"content: 'a;b'; color: red\">x</p></main>", 1, "color", "blue")).toBe(
      "<main><p style=\"content: 'a;b'; color: blue\">x</p></main>",
    );
  });

  it("refuses braces and semicolons in the value", () => {
    for (const value of ["red; color: blue", "red}", "{red"]) {
      expect(() => setStyleInSource("<main><p>x</p></main>", 1, "color", value)).toThrow(HtmlEditRefused);
    }
  });
});

describe("setTextInSource refusals carry their reason", () => {
  it("refuses void, unclosed and element-bearing targets", () => {
    const codes = (source: string, index: number) => {
      try {
        setTextInSource(source, index, "no");
        return "accepted";
      } catch (error) {
        return (error as HtmlEditRefused).code;
      }
    };
    expect(codes('<main><br><img src="asset:x"><svg><path/></svg><p>a<p>b</main>', 1)).toBe("htmlVoid");
    expect(codes('<main><br><img src="asset:x"><svg><path/></svg><p>a<p>b</main>', 4)).toBe("htmlVoid");
    expect(codes('<main><br><img src="asset:x"><svg><path/></svg><p>a<p>b</main>', 5)).toBe("htmlUnclosed");
    expect(codes("<main><p><b>x</b></p></main>", 1)).toBe("htmlTextOnly");
  });

  it("keeps bytes exact around unicode line separators, form feeds and bare carriage returns", () => {
    expect(setTextInSource("<main>a b\x0c<p>old\r</p>\r<p>keep</p></main>", 1, "new")).toBe(
      "<main>a b\x0c<p>new</p>\r<p>keep</p></main>",
    );
  });
});
