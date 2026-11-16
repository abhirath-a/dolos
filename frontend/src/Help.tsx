import { useEffect, useRef } from "react";
import { X } from "lucide-react";

export default function Help() {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    try {
      if (localStorage.getItem("dolos:help-seen") !== "1") dialog.current?.showModal();
    } catch {
      dialog.current?.showModal();
    }
    return () => dialog.current?.close();
  }, []);
  function close() {
    dialog.current?.close();
  }
  return (
    <>
      <button className="header-help-link" onClick={() => dialog.current?.showModal()}>
        Help
      </button>
      <dialog
        ref={dialog}
        className="help-dialog"
        aria-labelledby="help-title"
        onClose={() => {
          try {
            localStorage.setItem("dolos:help-seen", "1");
          } catch {
            /* Help remains available without storage. */
          }
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            const bounds = event.currentTarget.getBoundingClientRect();
            if (
              event.clientX < bounds.left ||
              event.clientX > bounds.right ||
              event.clientY < bounds.top ||
              event.clientY > bounds.bottom
            )
              close();
          }
        }}
      >
        <button className="icon-button help-close" aria-label="Close help" onClick={close}>
          <X size={20} />
        </button>
        <h2 id="help-title">Follow the Money...</h2>
        <p className="help-intro">Turn financial records into a network you can investigate.</p>
        <ol className="help-steps">
          <li>
            <strong>Choose your starting point.</strong>
            <p>
              Select a synthetic example in the header, or upload a batch of CSV statements.{" "}
              <a
                href="https://drive.google.com/drive/folders/1_wOnd3X4I4Ws3uKO-V3OOrPi05OkOCEL?usp=sharing"
                target="_blank"
              >
                Sample Data Here!
              </a>
            </p>
          </li>
          <li>
            <strong>Run the model.</strong>
            <p>
              Click Analyze network to run the combined GraphSAGE and XGBoost model. Each example
              recalculates when you analyze it.
            </p>
          </li>
          <li>
            <strong>Inspect a transfer.</strong>
            <p>
              Click a connection to highlight it in lime and open its result or source evidence. The
              analysis explains how graph context and transaction fields contributed.
            </p>
          </li>
          <li>
            <strong>Explore the network.</strong>
            <p>
              Scroll to pan, pinch or use the zoom buttons, and adjust X/Y spacing. After analysis,
              Highlight points of interest emphasizes flagged transfers. Flagged only hides the
              rest.
            </p>
          </li>
        </ol>
        <p className="help-note">
          A flag is a signal for review, not proof of money laundering. Examples use synthetic
          teaching data; context transfers may be unscored.
        </p>
        <button className="button primary" autoFocus onClick={close}>
          Explore the graph
        </button>
<br />
<br />
        <p>
          Developed by{" "}
          <a href="https://abhirath.net" target="_blank">
            Abhirath Agasanakoppa (abhi@abhirath.net)
          </a> and&nbsp;
          <a href="mailto:timmy.t.vu@gmail.com" target="_blank">
            Timothy Vu (timmy.t.vu@gmail.com)
          </a>, sophomores at Thomas Wootton High School in Rockville, Maryland, USA.
        </p>
      </dialog>
    </>
  );
}
