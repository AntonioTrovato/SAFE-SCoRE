import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import json
from pathlib import Path
import warnings
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

warnings.filterwarnings('ignore')

plt.rcParams.update({
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'font.size': 11,
    'axes.titlesize': 12,
    'axes.labelsize': 11,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.titlesize': 14,
    'font.family': 'serif',
    'text.usetex': False
})


class FuzzingToolAnalyzer:
    def __init__(self,
                 json_path: str = "results/tool_scores.json",
                 csv_path: str = "outputs/full_dataset_with_clusters.csv"):

        self.json_path = Path(json_path)
        self.csv_path = Path(csv_path)
        self.results_dir = Path("../../results/graphs")
        self.results_dir.mkdir(exist_ok=True)

        # Labels for the radar chart (criticality profile)
        self.metrics_labels = [
            'Collision Rate (CR)',
            'Violation Rate (VR)',
            'Completion Rate (Comp)',
            'Route Following (RF)',
            'Time Spent (TS)'
        ]

        # Configuration for the latent risk metrics (used by the boxplots)
        self.latent_risk_config = {
            'MDBV (m)': {'key': 'crit_MDBV', 'title': 'Minimum Distance Between Vehicles (MDBV)',
                         'ylabel': 'Distance (m)'},
            'min TTC (s)': {'key': 'crit_min_TTC', 'title': 'Minimum Time-to-Collision (TTC)', 'ylabel': 'Time (s)'},
            'TET (s)': {'key': 'crit_TET_total', 'title': 'Time-Exposed TTC (TET)', 'ylabel': 'Time (s)'}
        }

        # Configuration for plot colors and markers
        self.plot_config = {
            'colors': ['#2E86AB', '#A23B72', '#F18F01'],
            'markers': ['o', 's', '^']
        }

        # Load both data files
        self.scores_data = self._load_json_data()
        self.full_df = self._load_csv_data()
        self.tools_data = self._prepare_tool_data()

    def _load_json_data(self) -> dict:
        """Loads the aggregated data from the JSON file."""
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                logging.info(f"Loading aggregated data from: {self.json_path}")
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"CRITICAL ERROR: JSON file not found at '{self.json_path}'.")
            raise

    def _load_csv_data(self) -> pd.DataFrame:
        """Loads and validates the raw data from the CSV file."""
        try:
            df = pd.read_csv(self.csv_path)
            logging.info(f"Loading raw data from: {self.csv_path}")
            return df
        except FileNotFoundError:
            logging.error(f"CRITICAL ERROR: CSV file not found: {self.csv_path}")
            raise

    def _prepare_tool_data(self) -> dict:
        """Prepares a dictionary of DataFrames, one per tool, from the CSV."""
        tools = self.full_df['tool'].unique()
        return {tool: self.full_df[self.full_df['tool'] == tool] for tool in tools}

    def _prepare_data_from_json(self):
        """Transforms the JSON data into the formats used for the radar chart and table."""
        normalized_data = {}
        stats_summary = []

        for tool_name, data in self.scores_data.items():
            criticity = data['Criticity']

            # Data for the RADAR CHART (high values = more critical)
            normalized_data[tool_name] = [
                criticity.get('CollisionRate', 0),
                criticity.get('ViolationRate', 0),
                1.0 - criticity.get('CompletionRate', 1.0),
                1.0 - criticity.get('RFS', 1.0),
                criticity.get('TS', 0)
            ]

            # Data for the SUMMARY TABLE
            stats_summary.append({
                'Tool': tool_name,
                'Samples': data.get('total_scenarios', 'N/A'),
                'CR_raw': criticity.get('CollisionRate', 0),
                'VR_raw': criticity.get('ViolationRate', 0),
                'CoR_raw': criticity.get('CompletionRate', 0) * 100.0,
                'RF_raw': criticity.get('RFS', 0) * 100.0,
                'TE_raw': criticity.get('TS', 0) * 120.0,
                'Criticality_Index': criticity.get('C', 0),
                'Effectiveness': data.get('Effectiveness', {}).get('E', 0)
            })

        return normalized_data, pd.DataFrame(stats_summary)

    def plot_radar_chart(self, normalized_data: dict, save_path: Path):
        """Generates a radar chart of the criticality profile."""
        labels = self.metrics_labels
        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(10, 8), subplot_kw=dict(polar=True))

        for i, (tool_name, values) in enumerate(normalized_data.items()):
            values_closed = values + values[:1]
            ax.plot(angles, values_closed, 'o-', label=tool_name,
                    linewidth=2.5, color=self.plot_config['colors'][i],
                    marker=self.plot_config['markers'][i], markersize=8)
            ax.fill(angles, values_closed, alpha=0.15, color=self.plot_config['colors'][i])

        ax.set_ylim(0, 1)
        ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
        ax.set_yticklabels(['0.2', '0.4', '0.6', '0.8', '1.0'], size=9)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, size=11, weight='bold')
        ax.legend(loc='upper right', bbox_to_anchor=(1.2, 1.1), frameon=True)
        plt.title('Comparative Criticality Profile of the Tools',
                  size=14, weight='bold', y=1.15)

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
        plt.show()

    def plot_comparative_boxplots(self, save_path: Path):
        """Generates comparative box plots for the latent risk metrics."""
        fig, axes = plt.subplots(1, len(self.latent_risk_config), figsize=(15, 5))
        fig.suptitle('Latent Risk Analysis: Distribution of Near-Miss Metrics',
                     fontsize=14, weight='bold', y=1.02)
        tool_names = list(self.tools_data.keys())

        for idx, (metric_name, config) in enumerate(self.latent_risk_config.items()):
            ax = axes[idx]
            # Filter data for scenarios without collisions
            data_to_plot = [
                self.tools_data[tool][self.tools_data[tool]['ev_collision'] == 0][config['key']].dropna()
                for tool in tool_names
            ]
            ax.boxplot(data_to_plot, labels=tool_names, patch_artist=True,
                       boxprops=dict(facecolor=self.plot_config['colors'][idx], alpha=0.7),
                       medianprops=dict(color='darkred', linewidth=2))
            ax.set_title(config['title'], fontsize=12, weight='bold')
            ax.set_ylabel(config['ylabel'], fontsize=11)
            ax.grid(True, linestyle='--', alpha=0.6)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()

    def generate_latent_risk_table(self) -> pd.DataFrame:
        """
        Generates a table with descriptive statistics for the latent risk metrics.
        The analysis is performed only on scenarios without collisions.
        """
        summary_rows = []
        tools = list(self.tools_data.keys())

        # Iterate over each latent risk metric (MDBV, TTC, TET)
        for metric_name, config in self.latent_risk_config.items():
            # Iterate over each tool to compute its statistics for that metric
            for tool in tools:
                # Filter data: only scenarios without a collision for the current tool
                df_tool = self.tools_data[tool]
                non_collision_data = df_tool[df_tool['ev_collision'] == 0][config['key']].dropna()

                # If there is no data (e.g. all scenarios had a collision), create empty statistics
                if non_collision_data.empty:
                    stats = {
                        'count': 0, 'mean': np.nan, 'std': np.nan, 'min': np.nan,
                        '25%': np.nan, '50%': np.nan, '75%': np.nan, 'max': np.nan
                    }
                else:
                    # Use .describe() to get all statistics in one go
                    stats = non_collision_data.describe().to_dict()

                # Add a row to our summary table
                summary_rows.append({
                    'Metrica': metric_name,
                    'Tool': tool,
                    'Conteggio': int(stats.get('count', 0)),
                    'Media': stats.get('mean'),
                    'Dev. Std.': stats.get('std'),
                    'Min': stats.get('min'),
                    '25% (Q1)': stats.get('25%'),
                    '50% (Mediana)': stats.get('50%'),
                    '75% (Q3)': stats.get('75%'),
                    'Max': stats.get('max'),
                })

        # Create the final DataFrame and format the numbers for better readability
        results_df = pd.DataFrame(summary_rows).round(2)

        # Save the results to a CSV file
        results_df.to_csv(self.results_dir.parent / 'latent_risk_summary.csv', index=False)

        logging.info(f"Descriptive latent risk table saved to: {self.results_dir.resolve()}")
        return results_df

    def generate_summary_table(self, stats_df: pd.DataFrame) -> pd.DataFrame:
        """Generates and saves a formatted summary table"""
        summary = stats_df.sort_values('Criticality_Index', ascending=False).copy()
        summary['CR (%)'] = (summary['CR_raw'] * 100).round(1)
        summary['VR (%)'] = (summary['VR_raw'] * 100).round(1)
        summary['CoR (%)'] = summary['CoR_raw'].round(1)
        summary['RF (%)'] = summary['RF_raw'].round(1)
        summary['TE (s)'] = summary['TE_raw'].round(1)
        summary['CI'] = summary['Criticality_Index'].round(3)

        final_columns = ['Tool', 'Samples', 'CR (%)', 'VR (%)', 'CoR (%)', 'RF (%)', 'TE (s)', 'CI']
        summary_formatted = summary[final_columns]

        summary_formatted.to_csv(self.results_dir.parent / 'summary_table.csv', index=False)
        return summary_formatted

    def plot_effectiveness_barplot(self, stats_df: pd.DataFrame, save_path: Path):
        """Generates a bar plot for the effectiveness score E."""
        fig, ax = plt.subplots(figsize=(8, 6))

        colors = ['#2ca02c' if e > 0.7 else '#ff7f0e' if e > 0.4 else '#d62728'
                  for e in stats_df['Effectiveness']]

        bars = ax.bar(stats_df['Tool'], stats_df['Effectiveness'],
                      color=colors, alpha=0.8, edgecolor='black', linewidth=0.8)

        # Add value labels
        for bar in bars:
            height = bar.get_height()
            if not np.isnan(height) and height > 0:
                ax.annotate(f'{height:.3f}',
                            xy=(bar.get_x() + bar.get_width() / 2., height),
                            xytext=(0, 3),
                            textcoords="offset points",
                            ha='center', va='bottom',
                            fontsize=9, fontweight='bold')

        ax.set_title('Aggregate Effectiveness Score $E(T)$', pad=20, fontsize=12, weight='bold')
        ax.set_xlabel('Testing Tool', fontsize=11)
        ax.set_ylabel('Effectiveness $E(T)$', fontsize=11)
        ax.set_ylim(0, 1.1)
        ax.axhline(y=0.5, color='gray', linestyle='--', alpha=0.7, label='Average Threshold')
        ax.legend()
        ax.grid(True, linestyle='--', alpha=0.6)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()

    def plot_outcome_distribution(self, save_path: Path):
        """Generates a stacked bar chart for the outcome distribution."""

        def classify_outcome(row):
            if row["ev_collision"] > 0:
                return "Collision"
            elif (row.get("ev_red_light", 0) > 0 or row.get("ev_stop_sign", 0) > 0 or row.get("ev_speeding", 0) > 0):
                return "Violation"
            elif (row.get("crit_min_TTC", np.inf) < 1.5 or row.get("crit_MDBV", np.inf) < 2.0 or row.get(
                    "crit_TET_total", 0) > 0):
                return "Near-miss"
            else:
                return "Safe"

        self.full_df["Outcome"] = self.full_df.apply(classify_outcome, axis=1)
        outcome_counts = self.full_df.groupby(["tool", "Outcome"]).size().unstack(fill_value=0)
        outcome_pct = outcome_counts.div(outcome_counts.sum(axis=1), axis=0)

        # Ensure the correct column order
        outcome_pct = outcome_pct.reindex(columns=['Safe', 'Near-miss', 'Violation', 'Collision'], fill_value=0)

        ax = outcome_pct.plot(kind='bar', stacked=True, figsize=(10, 6),
                              color=['#2ca02c', '#17becf', '#ff7f0e', '#d62728'],
                              alpha=0.8, edgecolor='black', linewidth=0.8)

        ax.set_title("Outcome Distribution by Tool", pad=20, fontsize=12, weight='bold')
        ax.set_ylabel("Scenario Proportion", fontsize=11)
        ax.set_xlabel("Testing Tool", fontsize=11)
        ax.set_ylim(0, 1)
        ax.legend(title="Outcome", loc='center left', bbox_to_anchor=(1, 0.5))
        ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.8, axis='y')
        ax.set_axisbelow(True)
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right')

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()

    def plot_cluster_heatmap(self, save_path: Path):
        """Generates a heatmap for the cluster distribution."""
        if "cluster" not in self.full_df.columns:
            logging.warning("Column 'cluster' not found in the dataset. Skipping heatmap generation.")
            return

        cluster_counts = self.full_df.groupby(["tool", "cluster"]).size().reset_index(name="Count")
        pivot = cluster_counts.pivot(index="tool", columns="cluster", values="Count").fillna(0)
        pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100

        fig, ax = plt.subplots(figsize=(10, 6))
        im = ax.imshow(pivot_pct.values, cmap='Blues', aspect='auto')

        ax.set_xticks(range(len(pivot.columns)))
        ax.set_yticks(range(len(pivot.index)))
        ax.set_xticklabels([f"C{i}" for i in pivot.columns])
        ax.set_yticklabels(pivot.index)

        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                ax.text(j, i, f'{pivot_pct.iloc[i, j]:.1f}%',
                        ha="center", va="center", color="black", fontweight='bold')

        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Scenario Percentage (%)', rotation=270, labelpad=20)

        ax.set_title("Cluster Distribution by Tool", pad=20, fontsize=12, weight='bold')
        ax.set_ylabel("Testing Tool", fontsize=11)
        ax.set_xlabel("Cluster ID", fontsize=11)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()

    def run_complete_analysis(self):
        """Runs the complete analysis"""
        logging.info("Starting Comparative Analysis")

        # --- Part 1: Quick analysis from JSON ---
        logging.info("1. Generating aggregated metrics from JSON...")
        normalized_data, stats_df = self._prepare_data_from_json()

        self.plot_radar_chart(normalized_data, save_path=self.results_dir / 'criticality_profile.svg')
        summary_table = self.generate_summary_table(stats_df)
        self.plot_effectiveness_barplot(stats_df, save_path=self.results_dir / 'effectiveness_barplot.svg')

        # --- Part 2: Detailed analysis from CSV ---
        logging.info("2. Detailed latent risk analysis from CSV...")
        self.plot_comparative_boxplots(save_path=self.results_dir / 'latent_risk_analysis.svg')
        latent_risk_table = self.generate_latent_risk_table()

        # --- Part 3: Additional analyses ---
        logging.info("3. Generating additional analyses...")
        self.plot_outcome_distribution(save_path=self.results_dir / 'outcome_distribution.svg')
        self.plot_cluster_heatmap(save_path=self.results_dir / 'cluster_heatmap.svg')

        logging.info("Analysis completed successfully!")
        logging.info(f"Results have been saved to: {self.results_dir.resolve()}")

        print("\n" + "=" * 50 + "\nSummary Table (from JSON)\n" + "=" * 50)
        print(summary_table.to_string(index=False))
        print("=" * 50)

        print("\n" + "=" * 50 + "\nLatent Risk Descriptive Table (from CSV)\n" + "=" * 50)
        print(latent_risk_table.to_string(index=False))
        print("=" * 50)


def draw_graphs():
    """Main function to run the analysis."""
    try:
        analyzer = FuzzingToolAnalyzer(
            json_path="../../results/tool_scores.json",
            csv_path="../../outputs/full_dataset_with_clusters.csv"
        )
        analyzer.run_complete_analysis()
    except Exception as e:
        logging.error(f"Execution failed due to an error: {e}")


if __name__ == "__main__":
    draw_graphs()