import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "./icons";

/** The small set of building blocks the My page is made of: a label above a group, rows inside a group, and a sub page with a back button. */
export const SectionLabel = ({ children }: { children: ReactNode }) => <div className="section-label">{children}</div>;
export const Group = ({ children }: { children: ReactNode }) => <div className="menu-card">{children}</div>;

export function Row({ icon, label, value, onClick, danger }: { icon: ReactNode; label: string; value?: ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button type="button" className={`menu-row${danger ? " danger" : ""}`} onClick={onClick}>
      <span className="menu-icon">{icon}</span>
      <span className="menu-label">{label}</span>
      {value != null && value !== "" && <span className="menu-value">{value}</span>}
      {!danger && <span className="menu-chev"><ChevronRight /></span>}
    </button>
  );
}

export function SubPage({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  return (
    <div className="page subpage">
      <div className="subhead">
        <button type="button" className="back" onClick={onBack} aria-label="뒤로"><ChevronLeft /></button>
        <h3>{title}</h3>
        <span />
      </div>
      {children}
    </div>
  );
}
