#pragma once

#include "CoreMinimal.h"

// ---------------------------------------------------------------------------
// FUALFingerprint：纯数学、无状态、确定性的 16 维指纹工具。
//
// 所有产出向量长度恒为 kFingerprintDim(16)。布局（详见各 Build* 函数）：
//   * 网格类：归一化包围盒长宽比 3 + 顶点质心距离 8-bin 径向直方图 8 + 槽位特征 5 = 16
//   * 动画类：时长归一化 1 + RootMotion 方向余弦 3 + 四肢末端累计弧长占比 4
//             + 动能曲线 8-bin = 16
//
// 通用保证：
//   * 长度不足补零、超长截断；零向量返回全零；全程除零保护；不产生 NaN/Inf。
//   * 不加载任何 UObject / 贴图 / 网格，仅做纯数值运算。
// ---------------------------------------------------------------------------
struct FUALFingerprint
{
	// 指纹维度，固定为 16（与 DuckDB fingerprint_vec FLOAT[16] 对齐）。
	static constexpr int32 Dim = 16;

	/** 返回 16 个 0 的向量。 */
	static TArray<float> MakeEmpty();

	/** 原地 L2 归一化；零向量 / 极小向量保持全零（不产生 NaN）。 */
	static void NormalizeL2(TArray<float>& Vec);

	/** 余弦相似度；任一零向量返回 0；结果钳制到 [-1, 1]。 */
	static float CosineSimilarity(const TArray<float>& A, const TArray<float>& B);

	/**
	 * 顶点质心距离的径向直方图。
	 * 抗旋转：使用到质心的距离；抗等比缩放：用距离 / 最大距离分 bin。
	 * 返回长度 Bins 的 L1 归一化频率分布；空点集 / 重合点返回全零。
	 */
	static TArray<float> BuildRadialHistogram(const TArray<FVector>& Points, int32 Bins = 8);

	/**
	 * 网格类指纹：包围盒长宽比 3 + 径向直方图 8 + 槽位特征 5 = 恰好 16 维。
	 * Hist8 不足补零、超长截断。
	 */
	static TArray<float> BuildFromBoundsAndHistogram(
		const FVector& Size,
		const TArray<float>& Hist8,
		int32 MaterialSlots,
		int32 Sockets);

	/**
	 * 动画类指纹：时长 1 + RootMotion 方向余弦 3 + 四肢弧长占比 4 + 动能曲线 8 = 恰好 16 维。
	 * LimbArcLengths4 / KineticCurve8 不足补零、超长截断。
	 */
	static TArray<float> BuildFromAnimation(
		float Duration,
		const FVector& RootDelta,
		const TArray<float>& LimbArcLengths4,
		const TArray<float>& KineticCurve8);
};
