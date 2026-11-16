import type { ReactNode } from "react";

type CheckboxProps = {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  detail?: string;
  type?: "checkbox" | "radio";
};

export function Checkbox({ label, checked, onChange, disabled, detail, type = "checkbox" }: CheckboxProps) {
  return (
    <label className={disabled ? "check disabled" : "check"}>
      <input
        type={type}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
      {detail && <span className="check-detail">{detail}</span>}
    </label>
  );
}

type NumberFieldProps = {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  hint?: ReactNode;
};

export function NumberField({ id, label, value, min, max, onChange, hint }: NumberFieldProps) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isInteger(next) && next >= min && next <= max) {
            onChange(next);
          }
        }}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}
