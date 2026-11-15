export function Avatar({ name, size = 44 }: { name: string; size?: number }) {
  return (
    <div className="avatar" style={{ width: size, height: size, fontSize: size * 0.46 }} aria-hidden="true">
      {(name || "?").trim().slice(0, 1).toUpperCase()}
    </div>
  );
}
