#include "FUniversalReflectionExtractor.h"
#include "UALTaxonomyRules.h"

#include "UALTypes.h"
#include "UALFingerprint.h"

#include "AssetRegistry/AssetData.h"
#include "Containers/StringConv.h"
#include "Misc/SecureHash.h"
#include "Misc/DateTime.h"

#if defined(UAL_ENABLE_CDO_REFLECTION) && UAL_ENABLE_CDO_REFLECTION
#include "UObject/Object.h"
#include "UObject/UnrealType.h"
#endif

DEFINE_LOG_CATEGORY_STATIC(LogUALReflection, Log, All);

namespace
{
	// 常见 UE 命名前缀；仅按大小写敏感方式剥离，避免误伤普通单词。
	const TCHAR* GPrefixes[] =
	{
		TEXT("SM_"), TEXT("SK_"), TEXT("AM_"), TEXT("A_"),  TEXT("T_"),
		TEXT("MI_"), TEXT("M_"),  TEXT("BP_"), TEXT("WBP_"), TEXT("ABP_"),
		TEXT("NS_"), TEXT("PS_"), TEXT("DA_"), TEXT("AC_"),  TEXT("W_"),
		TEXT("S_"),  TEXT("B_")
	};

#if defined(UAL_ENABLE_CDO_REFLECTION) && UAL_ENABLE_CDO_REFLECTION
	// 安全顶层属性遍历：仅在显式开启 UAL_ENABLE_CDO_REFLECTION 时编译。
	// 注意：AssetData.GetAsset() 会加载资源，与"无头不加载"原则冲突，
	// 因此生产构建默认关闭；此处仅作为可选的 DataAsset / Blueprint CDO 反射来源。
	static void AppendCDOProperties(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
	{
		UObject* Loaded = AssetData.GetAsset();
		if (!Loaded)
		{
			return;
		}
		UClass* Cls = Loaded->GetClass();
		if (!Cls)
		{
			return;
		}

		int32 Count = 0;
		for (TFieldIterator<FProperty> It(Cls); It && Count < 64; ++It)
		{
			FProperty* Prop = *It;
			if (!Prop)
			{
				continue;
			}
			// 剔除大数组 / 容器 / 对象引用，避免递归与隐式资源加载。
			if (CastField<FArrayProperty>(Prop) || CastField<FMapProperty>(Prop) ||
				CastField<FSetProperty>(Prop)  || CastField<FObjectProperty>(Prop) ||
				CastField<FStructProperty>(Prop))
			{
				continue;
			}
			const FString Key = FString::Printf(TEXT("cdo.%s"), *Prop->GetName());
			OutRecord.InterfaceSpec.Add(Key, Prop->GetCPPType());
			++Count;
		}
		UE_LOG(LogUALReflection, Verbose, TEXT("CDO reflection appended %d props for %s"),
			Count, *AssetData.AssetName.ToString());
	}
#endif
}

bool FUniversalReflectionExtractor::CanExtract(const FAssetData& AssetData) const
{
	// 兜底：接受一切 UObject 资产，保证 FUALExtractorRegistry 永不空手而归。
	return true;
}

FString FUniversalReflectionExtractor::StripPrefix(const FString& AssetName)
{
	for (const TCHAR* P : GPrefixes)
	{
		const int32 Len = FCString::Strlen(P);
		if (AssetName.Len() > Len && AssetName.StartsWith(P, ESearchCase::CaseSensitive))
		{
			return AssetName.Mid(Len);
		}
	}
	return AssetName;
}

void FUniversalReflectionExtractor::TokenizeName(const FString& InName, TArray<FString>& OutTokens)
{
	OutTokens.Reset();

	FString Current;
	auto Flush = [&Current, &OutTokens]()
	{
		if (Current.Len() > 0)
		{
			OutTokens.Add(Current.ToLower());
			Current.Reset();
		}
	};

	for (int32 i = 0; i < InName.Len(); ++i)
	{
		const TCHAR C = InName[i];
		if (FChar::IsAlnum(C))
		{
			const bool bHasPrev = Current.Len() > 0;
			const TCHAR Prev = bHasPrev ? Current[Current.Len() - 1] : TEXT('\0');
			const bool bNextIsLower = (i + 1 < InName.Len()) && FChar::IsLower(InName[i + 1]);

			// 驼峰边界：小写/数字后接大写，或"缩写+单词"（大写后跟小写）处切分。
			if (FChar::IsUpper(C) && bHasPrev &&
				(FChar::IsLower(Prev) || FChar::IsDigit(Prev) ||
				 (FChar::IsUpper(Prev) && bNextIsLower)))
			{
				Flush();
			}
			// 字母 -> 数字 边界也切分
			else if (FChar::IsDigit(C) && bHasPrev && FChar::IsAlpha(Prev))
			{
				Flush();
			}
			Current.AppendChar(C);
		}
		else
		{
			// 下划线、连字符、空格、点等分隔符。
			Flush();
		}
	}
	Flush();

	// 去重（保持顺序）。
	TArray<FString> Unique;
	for (const FString& T : OutTokens)
	{
		Unique.AddUnique(T);
	}
	OutTokens = MoveTemp(Unique);
}

void FUniversalReflectionExtractor::BuildTaxonomy(const FString& AssetClass, TArray<FString>& OutTaxonomy)
{
	// 规则表由 Tools/gen_taxonomy.py 从 Server/ual_common/taxonomy.yaml 生成，
	// 与 Python 侧共用同一份数据；新增 UE 资产类型族只需改 YAML 后重新生成。
	// 大小写敏感匹配，保证 C++ 与 Python 的分类结果完全一致。
	using namespace UALTaxonomyRules;

	OutTaxonomy.Reset();
	OutTaxonomy.Add(GRoot);

	const TCHAR* Family = GFallback;
	for (int32 RuleIdx = 0; RuleIdx < GRuleCount; ++RuleIdx)
	{
		const FFamilyRule& Rule = GRules[RuleIdx];
		bool bMatched = false;
		for (int32 PatternIdx = 0; PatternIdx < Rule.PatternCount; ++PatternIdx)
		{
			if (AssetClass.Contains(Rule.Patterns[PatternIdx], ESearchCase::CaseSensitive))
			{
				bMatched = true;
				break;
			}
		}
		if (bMatched)
		{
			Family = Rule.Name;
			break;
		}
	}

	// 任何 UObject 派生类（含用户自定义类与新版引擎新增类型）都会得到三层 taxonomy；
	// 未命中规则时归入 Fallback 族，不会被丢弃。
	OutTaxonomy.AddUnique(FString(Family));
	OutTaxonomy.AddUnique(AssetClass);
}

FString FUniversalReflectionExtractor::ComputeContentHash(const FAssetData& AssetData)
{
	// 注意：这里刻意不哈希 .uasset 二进制。引擎重存、版本升级、序列化顺序变化
	// 都会改变二进制布局却不改变资产语义，导致哈希抖动。因此改用
	// asset_path + 排序后的 Tag-Value 元数据做 SHA1，得到稳定的"变更检测"信号。
	// 该哈希只用于 -Incremental 跳过未变更资产，绝不作为内容查重 / 去重依据
	//（查重请用 fingerprint_vec 余弦相似度）。
	const FString AssetPath = AssetData.PackageName.ToString();

	TArray<FString> Pairs;
	for (const auto& Tag : AssetData.TagsAndValues)
	{
		const FString Key = Tag.Key.ToString();
		Pairs.Add(Key + TEXT("=") + Tag.Value.AsString());
	}
	Pairs.Sort(); // 字典序，消除遍历顺序差异

	FString Payload = AssetPath;
	for (const FString& P : Pairs)
	{
		Payload += TEXT("\n");
		Payload += P;
	}

	FTCHARToUTF8 Utf8(*Payload);
	uint8 Digest[FSHA1::DigestSize];
	FSHA1::HashBuffer(reinterpret_cast<const uint8*>(Utf8.Get()),
		(uint32)Utf8.Length(), Digest);

	FString Hex;
	Hex.Reserve(FSHA1::DigestSize * 2);
	static const TCHAR HexChars[] = TEXT("0123456789abcdef");
	for (int32 i = 0; i < FSHA1::DigestSize; ++i)
	{
		Hex.AppendChar(HexChars[(Digest[i] >> 4) & 0x0F]);
		Hex.AppendChar(HexChars[Digest[i] & 0x0F]);
	}
	return Hex;
}

TArray<float> FUniversalReflectionExtractor::ComputeNameFingerprint(
	const FString& AssetPath, const FString& AssetName, const FString& AssetClass)
{
	TArray<float> Vec = FUALFingerprint::MakeEmpty(); // 16 个 0

	// 把路径一并纳入，保证"同路径同内容 => 稳定"，且不同路径天然可区分。
	const FString Key = AssetPath + TEXT("|") + AssetName + TEXT("|") + AssetClass;
	FTCHARToUTF8 Utf8(*Key);
	const int32 Len = Utf8.Length();
	const uint8* Bytes = reinterpret_cast<const uint8*>(Utf8.Get());

	if (Len > 0)
	{
		// [0] 名称/路径长度的有界编码。
		Vec[0] = (float)((double)Len / (1.0 + (double)Len));

		// [1..2] 元音 / 数字占比。
		int32 Vowels = 0;
		int32 Digits = 0;
		for (int32 i = 0; i < Key.Len(); ++i)
		{
			const TCHAR Lower = FChar::ToLower(Key[i]);
			if (Lower == TEXT('a') || Lower == TEXT('e') || Lower == TEXT('i') ||
				Lower == TEXT('o') || Lower == TEXT('u'))
			{
				++Vowels;
			}
			if (FChar::IsDigit(Key[i]))
			{
				++Digits;
			}
		}
		Vec[1] = (float)Vowels / (float)Key.Len();
		Vec[2] = (float)Digits / (float)Key.Len();

		// [3..10] 名称字节的 8-bin 直方图（按字节低 3 位分档）。
		for (int32 i = 0; i < Len; ++i)
		{
			const int32 Bin = ((int32)Bytes[i]) & 0x07;
			Vec[3 + Bin] += 1.0f;
		}
		float Sum = 0.0f;
		for (int32 i = 0; i < 8; ++i)
		{
			Sum += Vec[3 + i];
		}
		if (Sum > KINDA_SMALL_NUMBER)
		{
			for (int32 i = 0; i < 8; ++i)
			{
				Vec[3 + i] /= Sum;
			}
		}
	}

	// [11..15] 由类名累积出的确定性标量，∈[0,1]。
	uint32 Acc = 0;
	for (int32 i = 0; i < AssetClass.Len(); ++i)
	{
		Acc = Acc * 131u + (uint32)AssetClass[i];
	}
	for (int32 i = 0; i < 5; ++i)
	{
		const uint32 Shifted = Acc >> (i * 3);
		Vec[11 + i] = (float)((double)(Shifted & 0x7u) / 7.0);
	}

	// 归一化为单位向量，便于下游直接用余弦相似度。
	FUALFingerprint::NormalizeL2(Vec);
	return Vec;
}

void FUniversalReflectionExtractor::FillFromTags(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
{
	// 只读 Tag-Value，不加载 UObject。
	for (const auto& Tag : AssetData.TagsAndValues)
	{
		const FString Key = Tag.Key.ToString();
		const FString Raw = Tag.Value.AsString();
		if (Key.IsEmpty() || Raw.IsEmpty())
		{
			continue;
		}
		// FiBData / Thumbnail 是编辑器内部二进制 blob（Blueprint FiB 序列化、
		// 缩略图），无检索语义、体积大且含 C0/C1 控制字符，会破坏 staging YAML。
		if (Key.Equals(TEXT("FiBData"), ESearchCase::IgnoreCase) ||
			Key.Equals(TEXT("Thumbnail"), ESearchCase::IgnoreCase))
		{
			continue;
		}
		// 剔除超长值，避免把大数组 / blob 塞进 MAP。
		const FString Value = (Raw.Len() > 512) ? Raw.Left(512) : Raw;

		// 所有元数据都进 domain_spec，供下游领域分析。
		OutRecord.DomainSpec.Add(Key, Value);

		if (Key.Equals(TEXT("SequenceLength"), ESearchCase::IgnoreCase) ||
			Key.Equals(TEXT("Duration"), ESearchCase::IgnoreCase))
		{
			OutRecord.Duration = FCString::Atof(*Value);
		}
		else if (Key.Equals(TEXT("BoundsSize"), ESearchCase::IgnoreCase))
		{
			TArray<FString> Parts;
			Value.ParseIntoArrayWS(Parts);
			for (int32 i = 0; i < 3; ++i)
			{
				OutRecord.BoundsSize[i] = Parts.IsValidIndex(i) ? FCString::Atof(*Parts[i]) : 0.0f;
			}
		}
		else if (Key.Equals(TEXT("Skeleton"), ESearchCase::IgnoreCase))
		{
			OutRecord.SkeletonPath = Value;
		}
		else if (Key.Equals(TEXT("ParentClass"), ESearchCase::IgnoreCase) ||
				 Key.Equals(TEXT("GeneratedClass"), ESearchCase::IgnoreCase))
		{
			OutRecord.ParentClass = Value;
		}
		else if (Key.Contains(TEXT("Interface")) || Key.Contains(TEXT("Slot")) ||
				 Key.Contains(TEXT("Socket")))
		{
			OutRecord.InterfaceSpec.Add(Key, Value);
		}
	}
}

bool FUniversalReflectionExtractor::Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
{
	// 仅用 FAssetData 元数据，不触发任何资源加载。
	const FString AssetPath  = AssetData.PackageName.ToString();
	const FString AssetName  = AssetData.AssetName.ToString();
	const FString AssetClassPath = AssetData.AssetClassPath.ToString();
	FString AssetClass           = AssetClassPath;

	// UE5.1+ 的 FTopLevelAssetPath::ToString() 形如 "/Script/Engine.StaticMesh"，取末段得到干净类名。
	// 完整 class path 同时写入 tokens，避免不同模块下的同名 UClass 归一后无法区分。
	int32 DotIdx = INDEX_NONE;
	if (AssetClass.FindLastChar(TEXT('.'), DotIdx))
	{
		AssetClass = AssetClass.Mid(DotIdx + 1);
	}
	const FString BaseName = StripPrefix(AssetName);

	// 重置，确保 BoundsSize=3 / FingerprintVec=16 等定长数组就位。
	OutRecord = FUALAssetRecord();

	OutRecord.AssetPath    = AssetPath;
	OutRecord.AssetClass   = AssetClass;
	OutRecord.LogicalId    = FString::Printf(TEXT("%s:%s"), *AssetClass, *AssetName);
	OutRecord.ParentClass  = TEXT("");

	BuildTaxonomy(AssetClass, OutRecord.Taxonomy);
	TokenizeName(BaseName, OutRecord.Tokens);
	OutRecord.Tokens.AddUnique(AssetClass.ToLower());
	OutRecord.Tokens.AddUnique(AssetClassPath.ToLower());

	OutRecord.ContentHash    = ComputeContentHash(AssetData);
	OutRecord.FingerprintVec = ComputeNameFingerprint(AssetPath, AssetName, AssetClass);
	OutRecord.UpdatedAt      = FDateTime::UtcNow().ToIso8601(); // 时间戳不参与 content_hash

	FillFromTags(AssetData, OutRecord);

	// YAML 缓存由 Python 侧灌库阶段据 DuckDB 记录生成，兜底提取器留空。
	OutRecord.YamlCache = TEXT("");

#if defined(UAL_ENABLE_CDO_REFLECTION) && UAL_ENABLE_CDO_REFLECTION
	AppendCDOProperties(AssetData, OutRecord);
#endif

	UE_LOG(LogUALReflection, Verbose, TEXT("Extracted %s [%s]"), *AssetPath, *AssetClass);
	return true;
}
