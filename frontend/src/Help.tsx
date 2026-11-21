import { useRef } from "react";
import { X } from "lucide-react";

export default function Help({ onAbout }: { onAbout: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  function close() {
    dialog.current?.close();
  }
  return (
    <>
      <button
        className="header-help-link"
        onClick={() => dialog.current?.showModal()}
      >
        Help
      </button>
      <dialog
        ref={dialog}
        className="help-dialog"
        aria-labelledby="help-title"
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
        <button
          className="icon-button help-close"
          aria-label="Close help"
          onClick={close}
        >
          <X size={20} />
        </button>
        <h2 id="help-title">Investigate a network</h2>
        <p className="help-intro">
          Turn financial records into a network you can investigate.
        </p>
        <ol className="help-steps">
          <li>
            <strong>Choose your starting point.</strong>
            <p>
              Choose a synthetic example. When uploads are enabled, you can also
              explore PDF, CSV, or JSON statements.{" "}
              <a href="/dolos-video-statements.zip" download>
                Download sample PDFs
              </a>
            </p>
          </li>
          <li>
            <strong>Run the model.</strong>
            <p>
              Click Analyze network to run the combined GraphSAGE and XGBoost
              model. Each example recalculates when you analyze it.
            </p>
          </li>
          <li>
            <strong>Inspect a transfer.</strong>
            <p>
              Click a connection to highlight it in lime and open its result or
              source evidence. The analysis explains how graph context and
              transaction fields contributed.
            </p>
          </li>
          <li>
            <strong>Explore the network.</strong>
            <p>
              Pan or zoom the graph. Open Layout to adjust spacing. After
              analysis, use Emphasize flags to dim other transfers, or use
              Flagged only to hide them.
            </p>
          </li>
        </ol>
        <p className="help-note">
          A flag is a signal for review, not proof of money laundering. Examples
          use synthetic teaching data; context transfers may be unscored.
        </p>
        <div className="help-actions">
          <button className="button primary" autoFocus onClick={close}>
            Explore the graph
          </button>
          <button
            className="text-button"
            onClick={() => {
              close();
              onAbout();
            }}
          >
            About Dolos
          </button>
        </div>
      </dialog>
    </>
  );
}
