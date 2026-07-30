from clustering import run_clustering
from scores import compute_scores
from graphs import draw_graphs


def main():
    print("\n=== Starting scenario comparison ===\n")

    '''print("[1/3] Clustering scenarios in progress...")
    run_clustering()
    print("[OK] Clustering complete.\n")'''

    print("[2/3] Computing scores...")
    compute_scores()
    print("[OK] Scores computed.\n")

    print("[3/3] Generating graphs...")
    draw_graphs()
    print("[OK] Graphs saved.\n")

    print("=== Pipeline completed successfully ===")


if __name__ == "__main__":
    main()
