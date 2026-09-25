import DOMPurify from "dompurify";
import { marked } from "marked";
import { Fragment, type ReactNode, useMemo } from "react";

const ABAP_KEYWORDS = new Set(
  (
    "REPORT PROGRAM DATA TYPES TYPE CONSTANTS PARAMETERS SELECT-OPTIONS SELECT FROM WHERE INTO " +
    "TABLE APPENDING UP TO ROWS ORDER BY GROUP HAVING INNER JOIN LEFT OUTER ON AND OR NOT IN IS " +
    "INITIAL LOOP AT ENDLOOP IF ELSE ELSEIF ENDIF CASE WHEN ENDCASE DO ENDDO WHILE ENDWHILE " +
    "METHOD ENDMETHOD METHODS CLASS ENDCLASS DEFINITION IMPLEMENTATION PUBLIC PRIVATE PROTECTED " +
    "SECTION FINAL CREATE INHERITING RAISING TRY CATCH ENDTRY CALL FUNCTION EXPORTING IMPORTING " +
    "CHANGING RETURNING VALUE REF NEW START-OF-SELECTION END-OF-SELECTION FORM ENDFORM PERFORM " +
    "READ MODIFY INSERT DELETE UPDATE APPEND CLEAR REFRESH FIELD-SYMBOLS ASSIGN AUTHORITY-CHECK " +
    "MESSAGE RETURN EXIT CONTINUE CHECK INTERFACE ENDINTERFACE FOR TESTING DURATION SHORT RISK LEVEL HARMLESS"
  ).split(" "),
);

function highlight(line: string): ReactNode[] {
  if (/^\s*\*/.test(line)) return [<span key="c" className="tok-com">{line}</span>];
  const parts = line.split(/('(?:[^']|'')*'|`[^`]*`|\|[^|]*\||"[^\n]*$|[A-Za-z_][\w-]*)/);
  return parts.map((part, i) => {
    if (!part) return null;
    if (part.startsWith('"')) return <span key={i} className="tok-com">{part}</span>;
    if (/^['`|]/.test(part)) return <span key={i} className="tok-str">{part}</span>;
    if (ABAP_KEYWORDS.has(part.toUpperCase())) return <span key={i} className="tok-kw">{part}</span>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function Markdown({ source }: { source: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(source, { async: false })), [source]);
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

function Code({ source, abap }: { source: string; abap: boolean }) {
  return (
    <pre>
      {source.split("\n").map((line, i) => (
        <div key={i}>
          <span className="ln">{i + 1}</span>
          {abap ? highlight(line) : line}
        </div>
      ))}
    </pre>
  );
}

function prettyJson(source: string): string {
  try {
    return JSON.stringify(JSON.parse(source), null, 2);
  } catch {
    return source;
  }
}

export function CodeViewer({ path, source }: { path: string; source: string }) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".md")) return <Markdown source={source} />;
  if (lower.endsWith(".json")) return <Code source={prettyJson(source)} abap={false} />;
  return <Code source={source} abap={lower.endsWith(".abap")} />;
}
