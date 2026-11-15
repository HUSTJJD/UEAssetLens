#include "UALFingerprint.h"

namespace
{
	// 除零保护阈值：比 KINDA_SMALL_NUMBER 更小，避免误伤幅值很小但合法的向量。
	static constexpr double kEps = 1e-12;

	// 有限性判断（不依赖平台私有 API）：
	// NaN 与 ±Inf 经 Abs 后与 Max() 的比较都会返回 false，因此被统一视作 0。
	static FORCEINLINE bool IsFiniteValue(float V)
	{
		return FMath::Abs(V) <= TNumericLimits<float>::Max();
	}

	// 安全取第 Index 个值：越界补 0，非有限值当 0。
	static float SafeAt(const TArray<float>& Vec, int32 Index)
	{
		if (!Vec.IsValidIndex(Index))
		{
			return 0.0f;
		}
		const float V = Vec[Index];
		return IsFiniteValue(V) ? V : 0.0f;
	}

	static float SafeFloat(float V)
	{
		return IsFiniteValue(V) ? V : 0.0f;
	}

	// 统一规整：清掉 NaN/Inf，并保证长度恰好 Dim（不足补零，超长截断）。
	static void Sanitize(TArray<float>& Vec)
	{
		TArray<float> Fixed;
		Fixed.SetNumZeroed(FUALFingerprint::Dim);
		const int32 N = FMath::Min(Vec.Num(), FUALFingerprint::Dim);
		for (int32 i = 0; i < N; ++i)
		{
			Fixed[i] = IsFiniteValue(Vec[i]) ? Vec[i] : 0.0f;
		}
		Vec = MoveTemp(Fixed);
	}
}

TArray<float> FUALFingerprint::MakeEmpty()
{
	TArray<float> Vec;
	Vec.SetNumZeroed(Dim);
	return Vec;
}

void FUALFingerprint::NormalizeL2(TArray<float>& Vec)
{
	double SumSq = 0.0;
	for (int32 i = 0; i < Vec.Num(); ++i)
	{
		const float F = IsFiniteValue(Vec[i]) ? Vec[i] : 0.0f;
		SumSq += (double)F * (double)F;
	}

	// 零向量 / 非有限值主导：保持全零，绝不产生 NaN。
	if (!(SumSq > kEps))
	{
		for (int32 i = 0; i < Vec.Num(); ++i)
		{
			Vec[i] = 0.0f;
		}
		return;
	}

	const double Inv = 1.0 / FMath::Sqrt(SumSq);
	for (int32 i = 0; i < Vec.Num(); ++i)
	{
		const float F = IsFiniteValue(Vec[i]) ? Vec[i] : 0.0f;
		Vec[i] = (float)((double)F * Inv);
	}
}

float FUALFingerprint::CosineSimilarity(const TArray<float>& A, const TArray<float>& B)
{
	// 逐较短长度逐元素计算；长度不同的向量按公共前缀比较。
	const int32 N = FMath::Min(A.Num(), B.Num());
	if (N <= 0)
	{
		return 0.0f;
	}

	double Dot = 0.0;
	double NormA = 0.0;
	double NormB = 0.0;
	for (int32 i = 0; i < N; ++i)
	{
		const double a = (double)(IsFiniteValue(A[i]) ? A[i] : 0.0f);
		const double b = (double)(IsFiniteValue(B[i]) ? B[i] : 0.0f);
		Dot   += a * b;
		NormA += a * a;
		NormB += b * b;
	}

	// 任一为零向量 → 相似度 0（除零保护，不返回 NaN）。
	if (!(NormA > kEps) || !(NormB > kEps))
	{
		return 0.0f;
	}
	const double Denom = FMath::Sqrt(NormA) * FMath::Sqrt(NormB);
	if (!(Denom > kEps))
	{
		return 0.0f;
	}
	return (float)FMath::Clamp(Dot / Denom, -1.0, 1.0);
}

TArray<float> FUALFingerprint::BuildRadialHistogram(const TArray<FVector>& Points, int32 Bins)
{
	if (Bins <= 0)
	{
		Bins = 8;
	}

	TArray<float> Hist;
	Hist.SetNumZeroed(Bins);
	if (Points.Num() == 0)
	{
		return Hist; // 空输入 → 全零
	}

	// 质心：按分量累加，避免不同 UE 版本 FVector 标量除法签名差异。
	double Cx = 0.0, Cy = 0.0, Cz = 0.0;
	for (const FVector& P : Points)
	{
		Cx += (double)P.X;
		Cy += (double)P.Y;
		Cz += (double)P.Z;
	}
	const double InvN = 1.0 / (double)Points.Num();
	Cx *= InvN;
	Cy *= InvN;
	Cz *= InvN;

	// 各点到质心的距离，并求最大距离用于等比缩放归一化。
	TArray<double> Dists;
	Dists.Reserve(Points.Num());
	double MaxDist = 0.0;
	for (const FVector& P : Points)
	{
		const double dx = (double)P.X - Cx;
		const double dy = (double)P.Y - Cy;
		const double dz = (double)P.Z - Cz;
		const double d = FMath::Sqrt(dx * dx + dy * dy + dz * dz);
		Dists.Add(d);
		if (d > MaxDist)
		{
			MaxDist = d;
		}
	}

	// 所有点重合 / 坐标含 NaN → 全零（除零保护）。
	if (!(MaxDist > kEps))
	{
		return Hist;
	}

	for (double d : Dists)
	{
		const double Norm = FMath::Clamp(d / MaxDist, 0.0, 1.0);
		int32 Bin = (int32)(Norm * (double)Bins); // Norm >= 0，截断即 floor
		if (Bin >= Bins)
		{
			Bin = Bins - 1; // Norm == 1.0 归入最后一档
		}
		if (Bin < 0)
		{
			Bin = 0;
		}
		Hist[Bin] += 1.0f;
	}

	// L1 归一化为频率分布，使直方图与点数无关。
	float Total = 0.0f;
	for (float V : Hist)
	{
		Total += V;
	}
	if (Total > (float)kEps)
	{
		for (float& V : Hist)
		{
			V /= Total;
		}
	}
	return Hist;
}

TArray<float> FUALFingerprint::BuildFromBoundsAndHistogram(
	const FVector& Size,
	const TArray<float>& Hist8,
	int32 MaterialSlots,
	int32 Sockets)
{
	TArray<float> Out = MakeEmpty(); // 16 个 0

	// [0..2] 归一化包围盒长宽比 3 维：各边 / 最长边，∈[0,1]，等比缩放不变。
	const double sx = FMath::Abs((double)Size.X);
	const double sy = FMath::Abs((double)Size.Y);
	const double sz = FMath::Abs((double)Size.Z);
	const double maxDim = FMath::Max(FMath::Max(sx, sy), sz);
	if (maxDim > kEps)
	{
		Out[0] = (float)(sx / maxDim);
		Out[1] = (float)(sy / maxDim);
		Out[2] = (float)(sz / maxDim);
	}

	// [3..10] 顶点质心距离 8-bin 径向直方图（不足补零 / 超长截断）。
	for (int32 i = 0; i < 8; ++i)
	{
		Out[3 + i] = SafeAt(Hist8, i);
	}

	// [11..15] 槽位特征 5 维：对材质槽 / 插槽计数做确定性的有界标量编码。
	// 用 log(1+n) 压缩计数，再以 (1 + m + s) 归一化，保证分母 >= 1、无除零、无 NaN。
	const double m = FMath::Loge(1.0 + (double)FMath::Max(0, MaterialSlots));
	const double s = FMath::Loge(1.0 + (double)FMath::Max(0, Sockets));
	const double denom = 1.0 + m + s;
	Out[11] = (float)(m / denom);
	Out[12] = (float)(s / denom);
	Out[13] = (float)(1.0 / denom);
	Out[14] = (MaterialSlots + Sockets > 0) ? 1.0f : 0.0f;
	Out[15] = (float)((m + s) / denom);

	Sanitize(Out);
	return Out;
}

TArray<float> FUALFingerprint::BuildFromAnimation(
	float Duration,
	const FVector& RootDelta,
	const TArray<float>& LimbArcLengths4,
	const TArray<float>& KineticCurve8)
{
	TArray<float> Out = MakeEmpty(); // 16 个 0

	// [0] 时长归一化：dur/(1+dur) 严格单调、有界 [0,1)，相等时长得到相等值、不同时长必不等。
	const double dur = FMath::Abs((double)SafeFloat(Duration));
	Out[0] = (float)(dur / (1.0 + dur));

	// [1..3] RootMotion 方向余弦（单位向量）；零向量保持全零。
	const double rx = (double)RootDelta.X;
	const double ry = (double)RootDelta.Y;
	const double rz = (double)RootDelta.Z;
	const double rlen = FMath::Sqrt(rx * rx + ry * ry + rz * rz);
	if (rlen > kEps)
	{
		Out[1] = (float)(rx / rlen);
		Out[2] = (float)(ry / rlen);
		Out[3] = (float)(rz / rlen);
	}

	// [4..7] 四肢末端累计弧长占比 4 维（L1 归一化，除零保护）。
	TArray<float> Limb;
	Limb.SetNumZeroed(4);
	double LimbSum = 0.0;
	for (int32 i = 0; i < 4; ++i)
	{
		Limb[i] = SafeAt(LimbArcLengths4, i);
		LimbSum += FMath::Abs((double)Limb[i]);
	}
	if (LimbSum > kEps)
	{
		for (int32 i = 0; i < 4; ++i)
		{
			Out[4 + i] = (float)(FMath::Abs((double)Limb[i]) / LimbSum);
		}
	}

	// [8..15] 动能曲线 8-bin（不足补零 / 超长截断）。
	for (int32 i = 0; i < 8; ++i)
	{
		Out[8 + i] = SafeAt(KineticCurve8, i);
	}

	Sanitize(Out);
	return Out;
}
