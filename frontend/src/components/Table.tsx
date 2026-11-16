import type { ReactNode } from "react";

export type Column<T> = {
  key: string;
  label: string;
  numeric?: boolean;
  wrap?: boolean;
  render: (row: T) => ReactNode;
};

type TableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowClass?: (row: T) => string | undefined;
  label: string;
};

export function Table<T>({ columns, rows, rowKey, rowClass, label }: TableProps<T>) {
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? "num" : undefined} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className={rowClass?.(row)}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "num" : column.wrap ? "wrap" : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
