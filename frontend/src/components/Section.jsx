/** A dashboard section and its heading rule. This was the .sec-h / .hint pair
 *  in the old styles.css, repeated at six call sites; as a component the spacing and
 *  the underline can't drift between sections. */
export default function Section({ title, hint, children }) {
  return (
    <section className="mt-9">
      <div className="mb-3.5 flex items-baseline justify-between gap-4 border-b pb-2.5">
        <h2 className="text-[18px] font-[660] tracking-[-0.01em]">{title}</h2>
        {hint && <span className="text-[12.5px] text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </section>
  );
}
