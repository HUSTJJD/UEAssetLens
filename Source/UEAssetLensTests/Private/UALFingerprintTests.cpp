#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"
#include "Misc/AutomationTest.h"
#include "UALFingerprint.h"

// 测试模块需要一个模块入口才能被 UE 加载；这里用默认空实现。
IMPLEMENT_MODULE(FDefaultModuleImpl, UEAssetLensTests)

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	// 与实现一致的有限性判据：NaN 与 ±Inf 都会被判为 false。
	static bool IsFiniteLocal(float V)
	{
		return FMath::Abs(V) <= TNumericLimits<float>::Max();
	}

	static bool AllFinite(const TArray<float>& Vec)
	{
		for (float V : Vec)
		{
			if (FMath::IsNaN(V) || !IsFiniteLocal(V))
			{
				return false;
			}
		}
		return true;
	}

	static bool NearlyEqualArrays(const TArray<float>& A, const TArray<float>& B, float Tol)
	{
		if (A.Num() != B.Num())
		{
			return false;
		}
		for (int32 i = 0; i < A.Num(); ++i)
		{
			if (FMath::Abs(A[i] - B[i]) > Tol)
			{
				return false;
			}
		}
		return true;
	}

	static TArray<float> UniformHist(int32 Bins, float Value)
	{
		TArray<float> H;
		H.Init(Value, Bins);
		return H;
	}
}

// ---------------------------------------------------------------------------
// 1) 16 维长度 + 补零 / 超长截断
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALFingerprintDimTest,
	"UEAssetLens.Fingerprint.Dimensions",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALFingerprintDimTest::RunTest(const FString& Parameters)
{
	TestEqual(TEXT("MakeEmpty returns 16 dims"),
		FUALFingerprint::MakeEmpty().Num(), FUALFingerprint::Dim);

	const TArray<float> Hist8 = UniformHist(8, 0.125f);
	const TArray<float> Mesh = FUALFingerprint::BuildFromBoundsAndHistogram(
		FVector(100.0, 50.0, 25.0), Hist8, /*MaterialSlots=*/4, /*Sockets=*/2);
	TestEqual(TEXT("Mesh fingerprint is 16 dims"), Mesh.Num(), FUALFingerprint::Dim);
	TestTrue(TEXT("Mesh fingerprint has no NaN"), AllFinite(Mesh));

	const TArray<float> Limb4 = UniformHist(4, 1.0f);
	const TArray<float> Kin8 = UniformHist(8, 0.5f);
	const TArray<float> Anim = FUALFingerprint::BuildFromAnimation(
		2.0f, FVector(1.0, 0.0, 0.0), Limb4, Kin8);
	TestEqual(TEXT("Animation fingerprint is 16 dims"), Anim.Num(), FUALFingerprint::Dim);
	TestTrue(TEXT("Animation fingerprint has no NaN"), AllFinite(Anim));

	// 输入不足 -> 补零；输入超长 -> 截断；输出仍恰好 16 维。
	TArray<float> ShortHist;
	ShortHist.Add(1.0f);
	const TArray<float> Short = FUALFingerprint::BuildFromBoundsAndHistogram(
		FVector(1.0, 1.0, 1.0), ShortHist, 0, 0);
	TestEqual(TEXT("Short histogram still yields 16 dims"), Short.Num(), FUALFingerprint::Dim);

	const TArray<float> LongHist = UniformHist(64, 1.0f);
	const TArray<float> Long = FUALFingerprint::BuildFromBoundsAndHistogram(
		FVector(1.0, 1.0, 1.0), LongHist, 0, 0);
	TestEqual(TEXT("Long histogram still yields 16 dims"), Long.Num(), FUALFingerprint::Dim);

	const TArray<float> LongAnim = FUALFingerprint::BuildFromAnimation(
		5.0f, FVector::ZeroVector, UniformHist(64, 2.0f), UniformHist(64, 3.0f));
	TestEqual(TEXT("Long animation inputs still yield 16 dims"), LongAnim.Num(), FUALFingerprint::Dim);

	return true;
}

// ---------------------------------------------------------------------------
// 2) 零向量不产生 NaN（归一化 / 直方图 / 动画 / 余弦相似度）
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALFingerprintNoNaNTest,
	"UEAssetLens.Fingerprint.NoNaN",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALFingerprintNoNaNTest::RunTest(const FString& Parameters)
{
	TArray<float> Zero = FUALFingerprint::MakeEmpty();
	FUALFingerprint::NormalizeL2(Zero);
	TestTrue(TEXT("NormalizeL2(zero) stays finite"), AllFinite(Zero));
	for (float V : Zero)
	{
		TestEqual(TEXT("NormalizeL2(zero) stays all zero"), V, 0.0f);
	}

	TArray<FVector> EmptyPoints;
	const TArray<float> EmptyHist = FUALFingerprint::BuildRadialHistogram(EmptyPoints);
	TestEqual(TEXT("empty radial histogram has 8 bins"), EmptyHist.Num(), 8);
	TestTrue(TEXT("empty radial histogram has no NaN"), AllFinite(EmptyHist));

	// 所有点重合 -> 最大距离为 0 -> 全零，除零保护。
	TArray<FVector> Coincident;
	Coincident.Init(FVector(3.0, 4.0, 5.0), 4);
	TestTrue(TEXT("coincident points histogram has no NaN"),
		AllFinite(FUALFingerprint::BuildRadialHistogram(Coincident)));

	const TArray<float> Zero4 = UniformHist(4, 0.0f);
	const TArray<float> Zero8 = UniformHist(8, 0.0f);
	const TArray<float> ZeroAnim = FUALFingerprint::BuildFromAnimation(
		0.0f, FVector::ZeroVector, Zero4, Zero8);
	TestTrue(TEXT("zero animation has no NaN"), AllFinite(ZeroAnim));

	const float ZeroSim = FUALFingerprint::CosineSimilarity(Zero, Zero);
	TestFalse(TEXT("zero cosine similarity is not NaN"), FMath::IsNaN(ZeroSim));
	TestEqual(TEXT("zero cosine similarity is 0"), ZeroSim, 0.0f);

	return true;
}

// ---------------------------------------------------------------------------
// 3) 自相似度为 1.0
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALFingerprintSelfSimilarityTest,
	"UEAssetLens.Fingerprint.SelfSimilarity",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALFingerprintSelfSimilarityTest::RunTest(const FString& Parameters)
{
	const TArray<float> V = FUALFingerprint::BuildFromBoundsAndHistogram(
		FVector(1.0, 2.0, 3.0), UniformHist(8, 0.125f), 3, 1);
	const float Sim = FUALFingerprint::CosineSimilarity(V, V);
	TestTrue(TEXT("self similarity is ~1.0"), FMath::Abs(Sim - 1.0f) < KINDA_SMALL_NUMBER);

	const TArray<float> A = FUALFingerprint::BuildFromAnimation(
		1.5f, FVector(0.0, 1.0, 0.0), UniformHist(4, 2.0f), UniformHist(8, 0.25f));
	TestTrue(TEXT("animation self similarity is ~1.0"),
		FMath::Abs(FUALFingerprint::CosineSimilarity(A, A) - 1.0f) < KINDA_SMALL_NUMBER);

	return true;
}

// ---------------------------------------------------------------------------
// 4) 旋转不变性：同一组点绕 Z 轴旋转后径向直方图一致
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALFingerprintRotationInvarianceTest,
	"UEAssetLens.Fingerprint.RotationInvariance",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALFingerprintRotationInvarianceTest::RunTest(const FString& Parameters)
{
	TArray<FVector> Points;
	Points.Add(FVector(0.0, 0.0, 0.0));
	Points.Add(FVector(1.0, 0.0, 0.0));
	Points.Add(FVector(0.0, 2.0, 0.0));
	Points.Add(FVector(0.0, 0.0, 3.0));
	Points.Add(FVector(1.0, 1.0, 1.0));

	const TArray<float> H1 = FUALFingerprint::BuildRadialHistogram(Points, 8);

	// 绕 Z 轴旋转 37 度。
	const double Angle = FMath::DegreesToRadians(37.0);
	const double C = FMath::Cos(Angle);
	const double S = FMath::Sin(Angle);
	TArray<FVector> Rotated;
	for (const FVector& P : Points)
	{
		Rotated.Add(FVector(P.X * C - P.Y * S, P.X * S + P.Y * C, P.Z));
	}
	const TArray<float> H2 = FUALFingerprint::BuildRadialHistogram(Rotated, 8);

	TestTrue(TEXT("radial histogram is invariant under Z rotation"),
		NearlyEqualArrays(H1, H2, KINDA_SMALL_NUMBER));

	// 等比缩放也不改变直方图（额外覆盖抗缩放性质）。
	TArray<FVector> Scaled;
	for (const FVector& P : Points)
	{
		Scaled.Add(P * 10.0);
	}
	TestTrue(TEXT("radial histogram is invariant under uniform scaling"),
		NearlyEqualArrays(H1, FUALFingerprint::BuildRadialHistogram(Scaled, 8), KINDA_SMALL_NUMBER));

	return true;
}

// ---------------------------------------------------------------------------
// 5) 不同时长动画指纹不等
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALFingerprintDurationTest,
	"UEAssetLens.Fingerprint.DifferentDuration",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALFingerprintDurationTest::RunTest(const FString& Parameters)
{
	const TArray<float> Limb4 = UniformHist(4, 1.0f);
	const TArray<float> Kin8 = UniformHist(8, 0.5f);

	const TArray<float> Short = FUALFingerprint::BuildFromAnimation(
		1.0f, FVector(1.0, 0.0, 0.0), Limb4, Kin8);
	const TArray<float> Long = FUALFingerprint::BuildFromAnimation(
		3.0f, FVector(1.0, 0.0, 0.0), Limb4, Kin8);

	TestTrue(TEXT("different durations produce different fingerprints"),
		!NearlyEqualArrays(Short, Long, KINDA_SMALL_NUMBER));

	// 相同时长、相同其余输入 -> 完全相同的指纹（确定性）。
	const TArray<float> SameA = FUALFingerprint::BuildFromAnimation(
		2.0f, FVector(1.0, 0.0, 0.0), Limb4, Kin8);
	const TArray<float> SameB = FUALFingerprint::BuildFromAnimation(
		2.0f, FVector(1.0, 0.0, 0.0), Limb4, Kin8);
	TestTrue(TEXT("same inputs produce identical fingerprints"),
		NearlyEqualArrays(SameA, SameB, 0.0f));

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
