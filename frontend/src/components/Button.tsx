import type { ReactNode } from "react";

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  primary?: boolean;
  disabled?: boolean;
};

export function Button({ children, onClick, primary, disabled }: ButtonProps) {
  return (
    <button
      type="button"
      className={primary ? "button primary" : "button"}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
