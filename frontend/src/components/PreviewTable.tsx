import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';

interface PreviewTableProps {
  headers: string[];
  data: string[][];
}

export function PreviewTable({ headers, data }: PreviewTableProps) {
  return (
    <div className="border border-gray-300 bg-white">
      <Table>
        <TableHeader>
          <TableRow className="bg-gray-100">
            {headers.map((header, index) => (
              <TableHead key={index} className="text-gray-900">
                {header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.length > 0 ? (
            data.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <TableCell key={cellIndex} className="text-gray-700">
                    {cell || '—'}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={headers.length} className="text-center text-gray-500">
                No data available
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <div className="px-4 py-3 border-t border-gray-200 bg-gray-50">
        <p className="text-gray-500 text-sm">
          Showing first 5 rows of data
        </p>
      </div>
    </div>
  );
}
