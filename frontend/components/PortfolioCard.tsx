interface PortfolioCardProps {
  title: string;
  value: number;
  prefix?: string;
  color?: 'primary' | 'danger' | 'blue' | 'amber' | 'purple';
  icon?: React.ReactNode;
}

const colorClasses: Record<string, string> = {
  primary: 'bg-primary-50 text-primary-600 dark:bg-primary-900/20 dark:text-primary-400',
  danger: 'bg-danger-50 text-danger-600 dark:bg-danger-900/20 dark:text-danger-400',
  blue: 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400',
  amber: 'bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400',
  purple: 'bg-purple-50 text-purple-600 dark:bg-purple-900/20 dark:text-purple-400',
};

export function PortfolioCard({
  title,
  value,
  prefix = '₹',
  color = 'primary',
  icon,
}: PortfolioCardProps) {
  const formattedValue = new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);

  return (
    <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-dark-500 dark:text-dark-400">{title}</p>
          <p className="mt-1 text-2xl font-bold tabular-nums text-dark-900 dark:text-dark-50">
            {prefix}{formattedValue}
          </p>
        </div>
        {icon && <div className="p-2 rounded-lg bg-dark-100 dark:bg-dark-900">{icon}</div>}
      </div>
    </div>
  );
}