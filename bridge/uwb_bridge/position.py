import math


def project_to_plane(distance_m: float, height_diff_m: float) -> float:
    """Proyecta una distancia 3D al plano horizontal del tag."""
    if distance_m <= abs(height_diff_m):
        return 0.0
    return math.sqrt(distance_m**2 - height_diff_m**2)


def intersect(r_a: float, r_b: float, anchor_distance_m: float) -> tuple[float, float]:
    """Corte de los círculos centrados en A (0, 0) y B (D, 0), en el semiplano y >= 0.

    Si los círculos no se cortan, el punto cae sobre la línea de las anclas (y = 0).
    """
    x = (r_a**2 - r_b**2 + anchor_distance_m**2) / (2 * anchor_distance_m)
    y = math.sqrt(max(r_a**2 - x**2, 0.0))
    return x, y
